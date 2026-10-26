"""Night-season stage frames for Halloween: ofuda talisman strips laid end to
end around the image, each with a vermilion seal and a few ink strokes, and a
band of bones. The inner edge and shadow are
shared with paper_frames. Deterministic: the same image always gets the same
frame."""

import functools
import math
import random
from collections.abc import Callable

from PIL import Image, ImageDraw

from nani_pix_bot.services.cosmetics import paper_frames
from nani_pix_bot.services.shapes import bone_outline

Color = tuple[int, int, int]

SEED = 13
OFUDA_LENGTH = 6  # of a strip, in borders
OFUDA_OVERLAP = 0.3  # of the next strip, in borders
OFUDA_PAPER: Color = (238, 226, 196)
SEAL: Color = (196, 40, 32)
INK: Color = (30, 24, 28)
_TILT = 3.0  # degrees either way
_STRIP_HEIGHT = 1.4  # of the border: spills past the deckled edge, which trims it
_EDGE: Color = (190, 174, 138)  # a strip's end
_UNDERLAY: Color = (216, 203, 172)  # shows between two strips
_SEAL_SIZE = 0.72  # of the border
_STROKE_WIDTH = 0.15  # of the border
_STROKES = (2, 3)
_SS = 3
NIGHT: Color = (36, 28, 46)
BONE: Color = (236, 228, 204)
BONE_SHADE: Color = (190, 178, 150)
BONE_OUTLINE: Color = (70, 60, 50)
BONE_LENGTH = 4  # of a bone, in borders
_BONE_THICKNESS = 0.45  # of the border
_BONE_GAP = 0.6  # of the border, between the ends of two bones (their knobs fill it)
_BONE_SS = 4


def _strip(border: int, rng: random.Random) -> Image.Image:
    """One strip lying along x: paper, a seal and brush strokes, RGBA."""
    length = border * OFUDA_LENGTH
    height = round(border * _STRIP_HEIGHT)
    layer = Image.new("RGBA", (length * _SS, height * _SS), (*OFUDA_PAPER, 255))
    draw = ImageDraw.Draw(layer)
    for x in (0, layer.width - _SS):  # the two ends, so overlapping strips read apart
        draw.rectangle((x, 0, x + _SS - 1, layer.height), fill=_EDGE)
    seal = border * _SEAL_SIZE
    seal_x = rng.uniform(0.15, 0.85) * (length - seal)
    seal_y = (height - seal) / 2
    box = (seal_x, seal_y, seal_x + seal, seal_y + seal)
    draw.rectangle(tuple(v * _SS for v in box), fill=SEAL)
    width = max(1.2, border * _STROKE_WIDTH) * _SS
    for _ in range(rng.randint(*_STROKES)):
        x = rng.uniform(0.05, 0.95) * length
        if seal_x - border * 0.3 < x < seal_x + seal + border * 0.3:
            x = seal_x + seal + border * 0.5  # keep clear of the seal
        reach = border * rng.uniform(0.3, 0.42)
        lean = rng.uniform(-0.25, 0.25) * reach
        y = height / 2
        draw.line(
            ((x - lean) * _SS, (y - reach) * _SS, (x + lean) * _SS, (y + reach) * _SS),
            fill=INK,
            width=round(width),
        )
    return layer.resize((length, height), Image.Resampling.LANCZOS)


def _lay(
    canvas: Image.Image, strip: Image.Image, centre: tuple[float, float], angle: float
) -> None:
    turned = strip.rotate(angle, resample=Image.Resampling.BICUBIC, expand=True)
    canvas.alpha_composite(
        turned, (round(centre[0] - turned.width / 2), round(centre[1] - turned.height / 2))
    )


def _band(size: tuple[int, int], border: int, rng: random.Random) -> Image.Image:
    """Strips along the four sides, each running on from the last."""
    w, h = size
    canvas = Image.new("RGBA", size, (*_UNDERLAY, 255))
    pitch = border * (OFUDA_LENGTH - OFUDA_OVERLAP)
    half = border * OFUDA_LENGTH / 2
    mid = border * 0.6
    for side, run in enumerate((w, w, h, h)):
        count = max(1, math.ceil((run - border * OFUDA_OVERLAP) / pitch))
        for i in range(count):
            along = half + i * pitch
            tilt = rng.uniform(-_TILT, _TILT)
            strip = _strip(border, rng)
            if side == 0:
                _lay(canvas, strip, (along, mid), tilt)
            elif side == 1:
                _lay(canvas, strip, (along, h - mid), tilt)
            elif side == 2:
                _lay(canvas, strip, (mid, along), 90 + tilt)
            else:
                _lay(canvas, strip, (w - mid, along), 90 + tilt)
    return canvas


def _framed(
    image: Image.Image, paint: Callable[[tuple[int, int], int, random.Random], Image.Image]
) -> Image.Image:
    """`image` with the deckled, shadowed band `paint(size, border, rng)` draws."""
    base = image.convert("RGB")
    rng = random.Random(SEED)  # noqa: S311 - seeded for a reproducible look
    border = paper_frames.border_for(base.size)
    mask = paper_frames.deckle_mask(base.size, border, rng)
    band = paint(base.size, border, rng).convert("RGB")
    return Image.composite(band, paper_frames.shadowed(base, mask, border), mask)


def ofuda_strip(image: Image.Image) -> Image.Image:
    """The "ofuda_strip" frame: paper talismans with red seals and ink strokes."""
    return _framed(image, _band)


Point = tuple[float, float]


def _bone(draw: ImageDraw.ImageDraw, a: Point, b: Point, thick: float) -> None:
    """One ivory bone with an ink outline and a shade along its lower side."""
    outline = bone_outline(a, b, thick)
    if not outline:
        return
    draw.polygon(outline, fill=BONE, outline=BONE_OUTLINE, width=max(1, round(thick * 0.12)))
    length = math.hypot(b[0] - a[0], b[1] - a[1])
    nx, ny = -(b[1] - a[1]) / length, (b[0] - a[0]) / length
    if ny < 0 or (ny == 0 and nx < 0):
        nx, ny = -nx, -ny  # the shade always falls downwards
    off = thick * 0.26
    draw.line(
        (a[0] + nx * off, a[1] + ny * off, b[0] + nx * off, b[1] + ny * off),
        fill=BONE_SHADE,
        width=max(1, round(thick * 0.16)),
    )


def _pad(border: int) -> int:
    """Room round a bone for its knobs and outline."""
    return math.ceil(border * _BONE_THICKNESS * 1.3)


@functools.lru_cache(maxsize=32)
def _bone_sprite(border: int, length: int) -> Image.Image:
    """A bone lying along x, `length` between its end points."""
    pad, ss = _pad(border), _BONE_SS
    size = (length + 2 * pad, 2 * pad)
    tile = Image.new("RGBA", (size[0] * ss, size[1] * ss), (*NIGHT, 0))
    a, b = (pad * ss, pad * ss), ((pad + length) * ss, pad * ss)
    _bone(ImageDraw.Draw(tile), a, b, border * _BONE_THICKNESS * ss)
    return tile.resize(size, Image.Resampling.LANCZOS)


def _run(canvas: Image.Image, start: Point, end: Point, border: int, vertical: bool) -> None:
    """Bones end to end from `start` to `end`, the knobs of neighbours touching."""
    span = math.hypot(end[0] - start[0], end[1] - start[1])
    if span <= 0:
        return
    count = max(1, round(span / (border * (BONE_LENGTH + _BONE_GAP))))
    pitch = span / count
    length = round(max(0.0, pitch - border * _BONE_GAP))
    if length <= 0:
        return
    sprite = _bone_sprite(border, length)
    if vertical:
        sprite = sprite.transpose(Image.Transpose.ROTATE_90)
    for i in range(count):
        c = pitch * (i + 0.5)
        cx = start[0] + (end[0] - start[0]) * c / span
        cy = start[1] + (end[1] - start[1]) * c / span
        canvas.paste(sprite, (round(cx - sprite.width / 2), round(cy - sprite.height / 2)), sprite)


def _bone_band(size: tuple[int, int], border: int, _rng: random.Random) -> Image.Image:
    w, h = size
    canvas = Image.new("RGB", size, NIGHT)
    mid, b = border * 0.5, border
    # the top and bottom runs go on to the edges, so all four corners end the same way
    edge = b * 0.05
    _run(canvas, (edge, mid), (w - edge, mid), b, False)
    _run(canvas, (edge, h - mid), (w - edge, h - mid), b, False)
    _run(canvas, (mid, b), (mid, h - b), b, True)
    _run(canvas, (w - mid, b), (w - mid, h - b), b, True)
    return canvas


def bone_border(image: Image.Image) -> Image.Image:
    """The "bone_border" frame: ivory bones on a night band."""
    return _framed(image, _bone_band)
