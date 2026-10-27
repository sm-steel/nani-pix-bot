"""Night parade reveal, part 1 (Yōkai Night, seasons spec): six yōkai silhouettes march left
to right along the lower half of the image, each bobbing and swaying as it walks. Behind the
last walker the clear image shows, its edge softened by a band of mist; ahead of the leader the
front is dimmed, as if lit by the moon. The parade walks off the right edge and the image is
clear.

Everything but the walkers only varies by column, so the wipe and the dimming are blended in
narrow vertical bands (the mist) and plain pastes elsewhere. The sprites are scaled and
recoloured once per render and rotated per frame. A missing sprite never breaks a reveal: that
walker is skipped (the gap is logged once per sprite, see `_common.sprite_or_none`)."""

import math
import random
from collections.abc import Iterator
from typing import NamedTuple

from PIL import Image, ImageChops

from nani_pix_bot.services import sprites
from nani_pix_bot.services.reveal import encode
from nani_pix_bot.services.reveal.effects._common import ease_in_out, sprite_or_none

MARCH_S = 2.0
SILHOUETTE = (24, 16, 40)
RIM = (190, 200, 255)  # the moon's light on each walker's top edge, 1 px
WALKERS = (
    "parade/kasa_obake.png",
    "parade/chochin_obake.png",
    "parade/kitsune.png",
    "parade/oni.png",
    "parade/bakeneko.png",
    "parade/nopperabo.png",
)
HEIGHT = 0.42  # of the image's height
SPACING = 0.32  # of the width, between walkers
BASE = 0.97  # the walkers' feet, of the height
BOB = 0.03  # of the height
STRIDE_S = 0.35
TILT = 3.0  # degrees
MIST = 0.1  # of the width
MOONLIT = 0.8  # brightness of the front ahead of the leader
MOON_IN_S = 0.25  # the moonlight fades in, not a jump from the held front
PACE = 2.0  # ease-out power: the parade rushes in, then slows to a stroll as it leaves
SEED = 43
CRF = 23
MIN_WALKER_PX = 8


class _Walker(NamedTuple):
    art: Image.Image
    phase: float  # of a stride
    tilted: dict[int, tuple[Image.Image, Image.Image]]  # whole degrees -> (RGB, alpha)


class _Look(NamedTuple):
    clear: Image.Image
    front: Image.Image
    moonlit: Image.Image
    ramp: Image.Image  # 0 .. 255 across the mist, 1 px tall
    walkers: list[_Walker]  # leader first
    mist: int

    @property
    def tail_offset(self) -> float:
        """How far the last walker's left edge trails the leader's."""
        return max(0, len(self.walkers) - 1) * self.clear.width * SPACING

    @property
    def lead_width(self) -> int:
        return self.walkers[0].art.width if self.walkers else 0


def _walker_art(name: str, height: int) -> Image.Image | None:
    """The sprite as a flat silhouette with a 1 px moonlit rim on its top edge."""
    art = sprite_or_none(name, height, "skipping this walker")
    if art is None:
        return None
    flat = sprites.silhouette(art, SILHOUETTE)
    alpha = art.getchannel("A")
    lower = ImageChops.offset(alpha, 0, 1)
    lower.paste(0, (0, 0, alpha.width, 1))  # offset wraps the last row to the top
    rim = ImageChops.subtract(alpha, lower)
    flat.paste(RIM, (0, 0, flat.width, flat.height), rim)
    return flat


def _walkers(size: tuple[int, int]) -> list[_Walker]:
    height = round(size[1] * HEIGHT)
    if height < MIN_WALKER_PX:
        return []
    names = list(WALKERS)
    rng = random.Random(SEED)  # noqa: S311 - visual order, seeded for a reproducible look
    rng.shuffle(names)
    out = []
    for name in names:
        phase = rng.random()  # drawn per name, so a skipped sprite never shifts the others
        art = _walker_art(name, height)
        if art is not None:
            out.append(_Walker(art, phase, {}))
    return out


def _ramp(mist: int) -> Image.Image:
    ramp = Image.new("L", (mist, 1))
    ramp.putdata([round(255 * ease_in_out((i + 0.5) / mist)) for i in range(mist)])
    return ramp


def _look(original: Image.Image, stages: list[Image.Image]) -> _Look:
    clear = original.convert("RGB")
    front = stages[0].convert("RGB")
    mist = max(2, round(clear.width * MIST))
    return _Look(
        clear=clear,
        front=front,
        moonlit=front.point(lambda v: round(v * MOONLIT)),
        ramp=_ramp(mist),
        walkers=_walkers(clear.size),
        mist=mist,
    )


def _band(look: _Look, x: int, flip: bool, strength: float) -> tuple[int, Image.Image] | None:
    """The mist at columns `x`..`x + mist`, clipped to the image: (left edge, full-height mask),
    rising left to right, or falling when `flip`; None when it is off the image."""
    w, h = look.clear.size
    left, right = max(0, x), min(w, x + look.mist)
    if right <= left:
        return None
    row = look.ramp.transpose(Image.Transpose.FLIP_LEFT_RIGHT) if flip else look.ramp
    row = row.crop((left - x, 0, right - x, 1))
    if strength < 1:
        row = row.point(lambda v: round(v * strength))
    return left, row.resize((right - left, h), Image.Resampling.NEAREST)


def _paste_band(frame: Image.Image, top: Image.Image, band: tuple[int, Image.Image]) -> None:
    left, mask = band
    box = (left, 0, left + mask.width, mask.height)
    frame.paste(Image.composite(top.crop(box), frame.crop(box), mask), box[:2])


def _moonlight(frame: Image.Image, look: _Look, lead_right: int, strength: float) -> None:
    """The dimmed front ahead of the leader, fading in over its mist."""
    w, h = look.clear.size
    band = _band(look, lead_right, flip=False, strength=strength)
    if band is not None:
        _paste_band(frame, look.moonlit, band)
    start = max(0, lead_right + look.mist)
    if start >= w:
        return
    box = (start, 0, w, h)
    dim = look.moonlit.crop(box)
    if strength < 1:
        dim = Image.blend(look.front.crop(box), dim, strength)
    frame.paste(dim, box[:2])


def _wipe(frame: Image.Image, look: _Look, tail_left: int) -> None:
    """The clear image behind the last walker, mist at its trailing edge."""
    h = look.clear.height
    solid = min(look.clear.width, tail_left - look.mist)
    if solid > 0:
        frame.paste(look.clear.crop((0, 0, solid, h)), (0, 0))
    band = _band(look, tail_left - look.mist, flip=True, strength=1.0)
    if band is not None:
        _paste_band(frame, look.clear, band)


def _tilted(walker: _Walker, degrees: int) -> tuple[Image.Image, Image.Image]:
    """The walker tilted by whole degrees (a few steps, rotated once each and kept)."""
    if degrees not in walker.tilted:
        turned = walker.art.rotate(degrees, Image.Resampling.BILINEAR, expand=True)
        walker.tilted[degrees] = (turned.convert("RGB"), turned.getchannel("A"))
    return walker.tilted[degrees]


def _paste_walkers(frame: Image.Image, look: _Look, lead_left: float, seconds: float) -> None:
    w, h = look.clear.size
    for i, walker in enumerate(look.walkers):
        x = lead_left - i * w * SPACING
        if x >= w or x + walker.art.width <= 0:
            continue
        stride = seconds / STRIDE_S + walker.phase
        lift = abs(math.sin(math.pi * stride)) * BOB * h
        art, alpha = _tilted(walker, round(math.sin(2 * math.pi * stride) * TILT))
        at = (round(x + (walker.art.width - art.width) / 2), round(h * BASE - lift - art.height))
        frame.paste(art, at, alpha)


def frames(original: Image.Image, stages: list[Image.Image]) -> Iterator[bytes]:
    look = _look(original, stages)
    count = round(MARCH_S * encode.FPS)
    start = -float(look.lead_width)  # the leader just off the left edge
    end = (
        look.clear.width + look.mist + look.tail_offset
    )  # the last walker's mist clear of the right
    for i in range(count):
        k = i / count
        seconds = k * MARCH_S
        lead_left = start + (end - start) * (1 - (1 - k) ** PACE)
        frame = look.front.copy()
        _moonlight(frame, look, round(lead_left) + look.lead_width, min(1.0, seconds / MOON_IN_S))
        _wipe(frame, look, round(lead_left - look.tail_offset))
        _paste_walkers(frame, look, lead_left, seconds)
        yield frame.tobytes()
    yield look.clear.tobytes()  # end exactly on the clear frame


def render_part1(
    original: Image.Image, stages: list[Image.Image], mascot: encode.Still | None = None
) -> bytes:
    """Part 1 MP4: the front under the moon, the parade walking the clear image in behind it."""
    options = encode.Part1Options(crf=CRF, mascot=mascot)
    return encode.encode_part1(frames(original, stages), original.size, options)
