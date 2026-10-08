"""Worker entry points of the animated reveal (issue #295).

`pregenerate` runs at game start and renders the effect part as MPEG-TS; `finish` runs at reveal
time, adds the 2 s ending (clear image, plus the celebration badge for a win) and joins. Both run
in a spawned `ProcessPoolExecutor` worker, so they are top-level functions with picklable
arguments and results.
"""

import hashlib
import io
import time
from dataclasses import dataclass, field

from PIL import Image

from nani_pix_bot.models.enums import PixelAlgorithm, RevealEffect
from nani_pix_bot.services.pixelate import pixelate_image
from nani_pix_bot.services.reveal import celebration, encode
from nani_pix_bot.services.reveal.effects import EFFECTS
from nani_pix_bot.services.reveal.effects._common import to_yuv420

MAX_WIDTH = 1280


@dataclass(frozen=True)
class Pregen:
    """What `pregenerate` hands back: the effect part and where the ending must join it."""

    part1_ts: bytes
    join_offset: float
    render_ms: int


@dataclass(frozen=True)
class Badge:
    """The winner's celebration badge: avatar (None -> initial) and the verbatim label."""

    avatar: bytes | None
    handle: str


@dataclass
class _Static:
    """Winner-independent data of one image: its clear frame and the confetti per badge height."""

    size: tuple[int, int]
    clear_yuv: bytes
    confetti: dict[int, tuple[bytes, tuple[int, int]]] = field(default_factory=dict)


# Per-worker-process speed cache, NOT feature state (the DB slot is the state): it only keeps
# data that depends on nothing but the image, so `finish` skips re-decoding and re-rendering it
# right after `pregenerate`. At most one entry (the last image), keyed by a hash of the clear
# image bytes; a cold or replaced cache (worker restart) is rebuilt on demand.
_static: tuple[bytes, _Static] | None = None


def _key(clear: bytes) -> bytes:
    return hashlib.blake2b(clear, digest_size=16).digest()


def _load(image: bytes) -> Image.Image:
    """Decode, cap the width at MAX_WIDTH (LANCZOS) and crop to even dimensions (the effects and
    yuv420p need them)."""
    original = Image.open(io.BytesIO(image)).convert("RGB")
    if original.width > MAX_WIDTH:
        height = round(original.height * MAX_WIDTH / original.width)
        original = original.resize((MAX_WIDTH, height), Image.Resampling.LANCZOS)
    w, h = original.size
    return original.crop((0, 0, w - w % 2, h - h % 2))


def prep(
    image: bytes, algorithm: PixelAlgorithm, widths: tuple[int, ...]
) -> tuple[Image.Image, list[Image.Image]]:
    """The clear image (capped, even-sized) and its pixelated stages."""
    original = _load(image)
    return original, [pixelate_image(original, width, algorithm) for width in widths]


def _new_static(original: Image.Image) -> _Static:
    # to_yuv420 is what the effects use for their own frames, so the ending's clear frame matches
    # part 1's last one (bit-identical for iris, within a level or two of rounding, invisible,
    # for the other effects: no visible step at the join) and costs no ffmpeg run.
    return _Static(original.size, b"".join(to_yuv420(original)))


def _remember(clear: bytes, static: _Static) -> None:
    """The only writer of the cache."""
    global _static
    _static = (_key(clear), static)


def _confetti(static: _Static) -> bytes:
    """The confetti clip for this frame size's badge height, built once per entry."""
    height = celebration.badge_height(static.size)
    if height not in static.confetti:
        static.confetti[height] = celebration.confetti_clip(height)
    return static.confetti[height][0]


def _static_for(clear: bytes) -> _Static:
    cached = _static
    if cached is not None and cached[0] == _key(clear):
        return cached[1]
    static = _new_static(_load(clear))
    _remember(clear, static)
    return static


def pregenerate(
    image: bytes, algorithm: PixelAlgorithm, widths: tuple[int, ...], effect: RevealEffect
) -> Pregen:
    """Render the effect part for `image` as MPEG-TS and warm the static cache for it."""
    started = time.perf_counter()
    original, stages = prep(image, algorithm, widths)
    part1_ts, join_offset = encode.to_ts(EFFECTS[effect](original, stages))
    static = _new_static(original)
    _confetti(static)  # winner-independent: built now, so a win doesn't pay for it
    _remember(image, static)
    render_ms = round((time.perf_counter() - started) * 1000)
    return Pregen(part1_ts, join_offset, render_ms)


def _overlay(static: _Static, badge: Badge) -> encode.Overlay:
    height = celebration.badge_height(static.size)
    rgba, badge_size = celebration.badge_frames(
        celebration.make_badge(
            badge.avatar,
            badge.handle,
            height,
            celebration.badge_max_width(static.size[0], encode.Overlay.margin),
        )
    )
    return encode.Overlay(_confetti(static), rgba, badge_size)


def finish(pregen: Pregen, clear: bytes, badge: Badge | None) -> bytes:
    """The final MP4: part 1 plus the ending (with the celebration when there's a `badge`)."""
    static = _static_for(clear)
    overlay = None if badge is None else _overlay(static, badge)
    return encode.ending_and_join(
        pregen.part1_ts, pregen.join_offset, static.clear_yuv, static.size, overlay
    )


def warm_up() -> None:
    """No-op submit target: importing this module in a fresh worker is the whole point, so the
    first real job doesn't pay for the PIL/effects imports."""
