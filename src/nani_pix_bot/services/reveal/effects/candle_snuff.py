"""Candle snuff reveal, part 1 (Yōkai Night, seasons spec): the pixelated front sits in
darkness, lit only by a small warm circle around a candle's flame. The flame is snuffed out
and a wisp of smoke rises; the darkness lifts upward with the smoke, as a soft-edged mask
rising from the bottom, and the clear image shows below it, fully lit.

The darkness and light masks are drawn at quarter size and stretched (BILINEAR); the flame
is a teardrop gradient drawn once and scaled per frame; the smoke is seven small blurred puffs
drawn once and moved. Seeded, so the same image always flickers the same way. A missing
candle sprite never breaks a reveal: the flame (and its light) is drawn on its own, and the
gap is logged once per process (stdlib logging: the reveal worker has no bot-side log setup)."""

import logging
import math
import random
from collections.abc import Iterator
from typing import NamedTuple

from PIL import Image, ImageChops, ImageFilter

from nani_pix_bot.services import sprites
from nani_pix_bot.services.reveal import encode
from nani_pix_bot.services.reveal.effects._common import ease_in_out

FLICKER_S = 0.7  # the front, by candlelight
SNUFF_S = 0.25  # the flame shrinks to nothing, the smoke starts
LIFT_S = 1.0  # the darkness lifts upward with the smoke
FADE_S = 0.3  # the candle fades out over the last of the lift
DARK = 0.22  # brightness of the unlit front
LIGHT = 0.3  # radius of the candle's light, of the diagonal
FLICKER = 0.06  # of the light's radius
EDGE = 0.15  # soft edge of the rising darkness, of the height
CANDLE_X = 0.38  # of the width: bottom-centre-left, clear of Nani
CANDLE_H = 0.35  # of the height
CANDLE_BOTTOM = 0.04  # gap under the candle, of the height
CANDLE_DIM = 0.7  # the candle itself stands in the dark too
WICK_X = 71 / 135  # the wick's tip, of the sprite's width
FLAME_H = 0.075  # of the height
WARM = (255, 238, 205)  # the lit part of the front, a restrained warm
QUARTER = 4
SMOKE_BLOBS = 7  # small puffs strung into one thin wisp
SMOKE_ALPHA = 0.35  # peak opacity of a puff
SEED = 41
CRF = 23
CANDLE = "candle.png"
MIN_CANDLE_PX = 8

_missing_logged = False
_Strip = tuple[Image.Image, int, int]  # (1px-wide strip, window height, travel)


class _Scene(NamedTuple):
    size: tuple[int, int]
    flame: tuple[float, float]  # base of the flame, px
    candle: Image.Image | None
    candle_at: tuple[int, int]


class _Smoke(NamedTuple):
    mask: Image.Image  # L, blurred blob
    dx: float  # drift, px over the whole rise
    delay: float  # of the smoke's life
    strength: float


class _Look(NamedTuple):
    clear: Image.Image
    lit: Image.Image
    dark: Image.Image
    scene: _Scene
    radial: Image.Image
    flame: Image.Image
    smoke: list[_Smoke]
    strip: _Strip


def _candle(size: tuple[int, int]) -> tuple[Image.Image | None, tuple[int, int]]:
    """The candle sprite scaled to its height, dimmed, and where its top-left goes; None when
    it is too small to show or its file is missing."""
    global _missing_logged
    w, h = size
    height = round(h * CANDLE_H)
    if height < MIN_CANDLE_PX:
        return None, (0, 0)
    try:
        art = sprites.sprite_height(CANDLE, height)
    except OSError:
        if not _missing_logged:
            _missing_logged = True
            logging.getLogger(__name__).error(
                "candle art %s is missing — snuffing a bare flame", CANDLE
            )
        return None, (0, 0)
    dim = art.convert("RGB").point(lambda v: round(v * CANDLE_DIM))
    dim.putalpha(art.getchannel("A"))
    x = round(w * CANDLE_X - art.width * WICK_X)
    y = round(h * (1 - CANDLE_BOTTOM)) - height
    return dim, (x, y)


def _scene(size: tuple[int, int]) -> _Scene:
    w, h = size
    candle, at = _candle(size)
    return _Scene(size, (w * CANDLE_X, h * (1 - CANDLE_BOTTOM - CANDLE_H)), candle, at)


def _flame(height: int) -> Image.Image:
    """A teardrop, bright core to orange rim, as an RGBA gradient (drawn once)."""
    width = max(3, round(height * 0.45))
    rows: list[list[int]] = []
    cx, base = (width - 1) / 2, height * 0.72  # the round belly sits low, the tip points up
    for y in range(height):
        if y <= base:
            half = (width / 2) * math.sin(y / base * math.pi / 2) ** 1.4
        else:
            k = (y - base) / (height - base)
            half = (width / 2) * math.sqrt(max(0.0, 1 - k * k))
        rows.append(
            [
                round(255 * max(0.0, 1 - (abs(x - cx) / half) ** 2.2)) if half > 0 else 0
                for x in range(width)
            ]
        )
    ramp = Image.new("L", (width, height))
    ramp.putdata([v for row in rows for v in row])
    flame = Image.new("RGB", (width, height), (255, 150, 40))
    heat = ramp.point(lambda v: round(v * v * v / 255 / 255))
    flame.paste((255, 244, 200), (0, 0), heat)
    flame.putalpha(ramp.filter(ImageFilter.GaussianBlur(max(0.5, width / 14))))
    return flame


def _smoke(size: tuple[int, int], rng: random.Random) -> list[_Smoke]:
    w, h = size
    out = []
    for i in range(SMOKE_BLOBS):
        diameter = max(4, round(min(w, h) * rng.uniform(0.022, 0.034)))
        blob = Image.new("L", (diameter * 2, diameter * 2), 0)
        blob.paste(210, (diameter // 2, diameter // 2, diameter * 3 // 2, diameter * 3 // 2))
        out.append(
            _Smoke(
                mask=blob.filter(ImageFilter.GaussianBlur(diameter / 3.2)),
                dx=rng.uniform(-0.02, 0.02) * w,
                delay=i * 0.07,
                strength=rng.uniform(0.7, 1.0) * SMOKE_ALPHA,
            )
        )
    return out


def _grid(size: tuple[int, int]) -> tuple[int, int]:
    return max(1, size[0] // QUARTER), max(1, size[1] // QUARTER)


def _radial() -> Image.Image:
    """A 255-at-centre, 0-at-rim falloff disc (256 px, eased)."""
    base = Image.radial_gradient("L")  # 0 at the centre .. 255 at the rim
    return base.point(lambda v: round(255 * (1 - ease_in_out(v / 255)) ** 1.2))


def _light_mask(scene: _Scene, radius: float, strength: float, radial: Image.Image) -> Image.Image:
    """255 where the flame lights the front: a soft circle drawn at quarter size."""
    gw, gh = _grid(scene.size)
    side = max(2, round(2 * radius / QUARTER))
    disc = radial.resize((side, side), Image.Resampling.BILINEAR)
    if strength < 1:
        disc = disc.point(lambda v: round(v * strength))
    canvas = Image.new("L", (gw, gh), 0)
    cx, cy = scene.flame[0] / QUARTER, scene.flame[1] / QUARTER
    canvas.paste(disc, (round(cx - side / 2), round(cy - side / 2)))
    return canvas.resize(scene.size, Image.Resampling.BILINEAR)


def _lift_strip(size: tuple[int, int]) -> _Strip:
    """A 1px-wide strip, 0 above the edge and 255 below it; sliding a window up it lifts
    the darkness."""
    gh = _grid(size)[1]
    edge = max(2, round(gh * EDGE))
    strip = Image.new("L", (1, 2 * gh + edge), 0)
    strip.putdata(
        [
            round(255 * ease_in_out(min(1.0, max(0.0, (i - gh) / edge))))
            for i in range(2 * gh + edge)
        ]
    )
    return strip, gh, gh + edge


def _lifted(look: _Look, k: float) -> Image.Image:
    """The clear image below the rising edge, the darkness above it, `k` of the way up. The mask
    only varies by row, so just the soft edge band is blended; the rest is a plain paste."""
    w, h = look.scene.size
    image, window, travel = look.strip
    top = round(k * travel)
    rows = image.crop((0, top, 1, top + window)).resize((1, h), Image.Resampling.BILINEAR)
    started = rows.point(lambda v: 255 if v else 0).getbbox()  # rows where any clear shows
    if started is None:
        return look.dark.copy()
    unfinished = rows.point(lambda v: 255 if v < 255 else 0).getbbox()  # rows not yet fully clear
    lo = started[1]
    hi = unfinished[3] if unfinished else lo
    frame = look.dark.copy()
    if hi < h:
        frame.paste(look.clear.crop((0, hi, w, h)), (0, hi))
    if hi > lo:
        band = rows.crop((0, lo, 1, hi)).resize((w, hi - lo), Image.Resampling.NEAREST)
        box = (0, lo, w, hi)
        frame.paste(Image.composite(look.clear.crop(box), look.dark.crop(box), band), (0, lo))
    return frame


def _flicker(rng: random.Random, count: int) -> list[float]:
    """Smoothed seeded noise in [-1, 1], one value per frame."""
    raw = [rng.uniform(-1, 1) for _ in range(count + 2)]
    return [(raw[i] + raw[i + 1] * 2 + raw[i + 2]) / 4 for i in range(count)]


def _paste_flame(frame: Image.Image, look: _Look, scale: float, sway: float) -> None:
    if scale <= 0.02:
        return
    flame = look.flame
    height = max(1, round(flame.height * scale))
    width = max(1, round(flame.width * (0.8 + 0.2 * scale)))
    shown = flame.resize((width, height), Image.Resampling.BILINEAR)
    fx, fy = look.scene.flame
    frame.paste(shown.convert("RGB"), (round(fx - width / 2 + sway), round(fy - height)), shown)


def _paste_smoke(frame: Image.Image, look: _Look, k: float) -> None:
    """The wisp; `k` is how far into its life the smoke is (0 at the snuff, 1 at the end)."""
    w, h = look.scene.size
    fx, fy = look.scene.flame
    for blob in look.smoke:
        life = (k - blob.delay) / (1 - blob.delay)
        if life <= 0 or life >= 1:
            continue
        rise = ease_in_out(life) * (fy + h * 0.1)
        alpha = blob.strength * math.sin(life * math.pi) ** 0.8
        mask = blob.mask.point(lambda v, a=alpha: round(v * a))
        x = fx + blob.dx * life + math.sin(life * 6 + blob.delay * 9) * w * 0.02 * (0.3 + life)
        frame.paste(
            (190, 190, 200),
            (round(x - mask.width / 2), round(fy - rise - mask.height / 2)),
            mask,
        )


def _paste_candle(frame: Image.Image, look: _Look, fade: float) -> None:
    candle = look.scene.candle
    if candle is None or fade <= 0:
        return
    mask = candle.getchannel("A")
    if fade < 1:
        mask = mask.point(lambda v: round(v * fade))
    frame.paste(candle.convert("RGB"), look.scene.candle_at, mask)


def _look(original: Image.Image, stages: list[Image.Image]) -> _Look:
    clear = original.convert("RGB")
    front = stages[0].convert("RGB")
    size = clear.size
    rng = random.Random(SEED)  # noqa: S311 - visual randomness, seeded for a reproducible look
    return _Look(
        clear=clear,
        lit=ImageChops.multiply(front, Image.new("RGB", size, WARM)),
        dark=front.point(lambda v: round(v * DARK)),
        scene=_scene(size),
        radial=_radial(),
        flame=_flame(max(4, round(size[1] * FLAME_H))),
        smoke=_smoke(size, rng),
        strip=_lift_strip(size),
    )


def _vigil(look: _Look, count: int, snuff_from: int) -> Iterator[bytes]:
    """The candlelit front: the flame flickers, then (from frame `snuff_from`) is snuffed."""
    size = look.scene.size
    diagonal = math.hypot(*size)
    noise = _flicker(random.Random(SEED + 1), count)  # noqa: S311 - seeded, visual only
    total = SNUFF_S + LIFT_S
    for i in range(count):
        snuff = max(0.0, (i - snuff_from + 1) / (count - snuff_from))
        eased = ease_in_out(snuff)
        radius = LIGHT * diagonal * (1 + FLICKER * noise[i]) * (1 - 0.45 * eased)
        light = _light_mask(look.scene, radius, 1 - 0.8 * eased, look.radial)
        frame = Image.composite(look.lit, look.dark, light)
        _paste_candle(frame, look, 1.0)
        _paste_flame(frame, look, (1 - snuff) * (1 + 0.08 * noise[i]), noise[i] * size[0] * 0.003)
        if snuff > 0:
            _paste_smoke(frame, look, snuff * SNUFF_S / total)
        yield frame.tobytes()


def _lift(look: _Look, count: int) -> Iterator[bytes]:
    """The darkness lifting off the clear image, upward with the smoke."""
    fade = max(1, round(FADE_S * encode.FPS))
    for j in range(count):
        k = (j + 1) / count
        frame = _lifted(look, ease_in_out(k))
        _paste_candle(frame, look, min(1.0, (count - 1 - j) / fade))
        _paste_smoke(frame, look, (SNUFF_S + k * LIFT_S) / (SNUFF_S + LIFT_S))
        yield frame.tobytes()


def frames(original: Image.Image, stages: list[Image.Image]) -> Iterator[bytes]:
    look = _look(original, stages)
    flicker, snuff, lift = (round(s * encode.FPS) for s in (FLICKER_S, SNUFF_S, LIFT_S))
    yield from _vigil(look, flicker + snuff, flicker)
    yield from _lift(look, lift)
    yield look.clear.tobytes()  # end exactly on the clear frame


def render_part1(
    original: Image.Image, stages: list[Image.Image], mascot: encode.Still | None = None
) -> bytes:
    """Part 1 MP4: the pixelated front by candlelight, snuffed, the darkness lifting off the
    clear image."""
    options = encode.Part1Options(crf=CRF, mascot=mascot)
    return encode.encode_part1(frames(original, stages), original.size, options)
