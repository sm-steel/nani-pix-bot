"""Worker entry points of the animated reveal (issue #295).

`pregenerate` runs at game start and renders the effect part as MPEG-TS; `finish` runs at reveal
time, adds the 2 s ending (clear image, plus the celebration badge for a win) and joins. Both run
in a spawned `ProcessPoolExecutor` worker, so they are top-level functions with picklable
arguments and results.
"""

import hashlib
import io
import logging
import time
from dataclasses import dataclass, field

from PIL import Image

from nani_pix_bot.models.enums import PixelAlgorithm, RevealEffect
from nani_pix_bot.services import mascot
from nani_pix_bot.services.mascot import Pose
from nani_pix_bot.services.pixelate import pixelate_image
from nani_pix_bot.services.reveal import celebration, encode, overlays
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
    """The winner's celebration badge: avatar (None -> initial), the verbatim label and the key
    of the win overlay (a plain str: this is pickled into the worker)."""

    avatar: bytes | None
    handle: str
    overlay: str = overlays.DEFAULT_OVERLAY


@dataclass
class _Static:
    """Winner-independent data of one image: its clear frame and the win-overlay clips, keyed by
    (overlay, badge height)."""

    size: tuple[int, int]
    clear_yuv: bytes
    confetti: dict[tuple[str, int], tuple[bytes, tuple[int, int]]] = field(default_factory=dict)


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


def _confetti(static: _Static, overlay: str = overlays.DEFAULT_OVERLAY) -> bytes:
    """The clip of `overlay` for this frame size's badge height, built once per entry."""
    height = celebration.badge_height(static.size)
    if (overlay, height) not in static.confetti:
        static.confetti[overlay, height] = overlays.WIN_OVERLAYS[overlay](height)
    return static.confetti[overlay, height][0]


def _overlay_clip(static: _Static, overlay: str) -> bytes:
    """The clip of the winner's overlay; an unknown key or a renderer that raises falls back to
    the default confetti. The worker has no bot-side log setup (and imports no logger package),
    so the warning goes through stdlib logging to stderr."""
    try:
        return _confetti(static, overlay)
    except Exception:
        if overlay == overlays.DEFAULT_OVERLAY:
            raise
        logging.getLogger(__name__).warning(
            "win overlay %r is unknown or failed to render; using the default",
            overlay,
            exc_info=True,
        )
        return _confetti(static)


def _draw_ahead(static: _Static, keys: tuple[str, ...]) -> None:
    """Build the clips of `keys` (the overlays players have equipped) into the entry now,
    so whoever wins doesn't wait for theirs. One that fails is skipped with a warning: the
    win then draws it, or falls back to the default, as it would have anyway."""
    for key in keys:
        try:
            _confetti(static, key)
        except Exception:
            logging.getLogger(__name__).warning(
                "couldn't draw win overlay %r ahead; it'll be drawn at the win", key, exc_info=True
            )


def _static_for(clear: bytes) -> _Static:
    cached = _static
    if cached is not None and cached[0] == _key(clear):
        return cached[1]
    static = _new_static(_load(clear))
    _remember(clear, static)
    return static


def pregenerate(
    image: bytes,
    algorithm: PixelAlgorithm,
    widths: tuple[int, ...],
    effect: RevealEffect,
    win_overlays: tuple[str, ...] = (),
) -> Pregen:
    """Render the effect part for `image` as MPEG-TS and warm the static cache for it,
    with the default overlay and `win_overlays` (those equipped) already drawn."""
    started = time.perf_counter()
    original, stages = prep(image, algorithm, widths)
    part1_ts, join_offset = encode.to_ts(
        EFFECTS[effect](original, stages, _still(Pose.CAST, original.size))
    )
    static = _new_static(original)
    _confetti(static)  # winner-independent: built now, so a win doesn't pay for it
    _draw_ahead(static, win_overlays)
    _remember(image, static)
    render_ms = round((time.perf_counter() - started) * 1000)
    return Pregen(part1_ts, join_offset, render_ms)


def _still(pose: Pose, size: tuple[int, int]) -> encode.Still:
    """Nani Pix in `pose`, sized for a frame of `size` (issue #461)."""
    return encode.Still(*mascot.still_png(pose, size))


def _overlay(static: _Static, badge: Badge, ending: Pose) -> encode.Overlay:
    """The winner's badge, kept short of Nani's corner however long the name."""
    height = celebration.badge_height(static.size)
    reserved = static.size[0] - mascot.box(static.size, ending)[0]
    rgba, badge_size = celebration.badge_frames(
        celebration.make_badge(
            badge.avatar,
            badge.handle,
            height,
            celebration.badge_max_width(static.size[0], encode.Overlay.margin, reserved),
        )
    )
    return encode.Overlay(_overlay_clip(static, badge.overlay), rgba, badge_size)


def finish(pregen: Pregen, clear: bytes, badge: Badge | None, ending: Pose) -> bytes:
    """The final MP4: part 1 plus the ending (with the celebration when there's a `badge`),
    Nani in the `ending` pose on top of it."""
    static = _static_for(clear)
    overlay = None if badge is None else _overlay(static, badge, ending)
    layers = encode.EndingLayers(overlay, _still(ending, static.size))
    return encode.ending_and_join(
        pregen.part1_ts, pregen.join_offset, static.clear_yuv, static.size, layers
    )


def preview_clip(
    image: bytes,
    algorithm: PixelAlgorithm,
    widths: tuple[int, ...],
    effect: RevealEffect,
    badge: Badge | None,
) -> bytes:
    """A whole reveal MP4 in one go, for a cosmetic's preview (seasons spec §3), ending like
    a won game. Unlike `pregenerate`, it leaves the static cache alone: that entry belongs to
    the game in progress, whose win must not pay to rebuild it."""
    original, stages = prep(image, algorithm, widths)
    part1_ts, join_offset = encode.to_ts(
        EFFECTS[effect](original, stages, _still(Pose.CAST, original.size))
    )
    static = _new_static(original)
    overlay = None if badge is None else _overlay(static, badge, Pose.WIN)
    layers = encode.EndingLayers(overlay, _still(Pose.WIN, static.size))
    return encode.ending_and_join(part1_ts, join_offset, static.clear_yuv, static.size, layers)


def warm_up() -> None:
    """No-op submit target: importing this module in a fresh worker is the whole point, so the
    first real job doesn't pay for the PIL/effects imports."""
