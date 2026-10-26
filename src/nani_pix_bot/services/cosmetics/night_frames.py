"""Night-season stage frames for Halloween: ofuda talisman strips laid end to
end around the image, each with a vermilion seal and a few ink strokes. The
inner edge and shadow are shared with paper_frames. Deterministic: the same
image always gets the same strips."""

import math
import random

from PIL import Image, ImageDraw

from nani_pix_bot.services.cosmetics import paper_frames

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
            x = (seal_x + seal + border * 0.5) % length  # keep clear of the seal
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


def ofuda_strip(image: Image.Image) -> Image.Image:
    """The "ofuda_strip" frame: paper talismans with red seals and ink strokes."""
    base = image.convert("RGB")
    rng = random.Random(SEED)  # noqa: S311 - seeded for a reproducible look
    border = paper_frames.border_for(base.size)
    mask = paper_frames.deckle_mask(base.size, border, rng)
    band = _band(base.size, border, rng).convert("RGB")
    return Image.composite(band, paper_frames.shadowed(base, mask, border), mask)
