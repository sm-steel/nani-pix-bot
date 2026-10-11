"""Paper-like stage frames for Sakura Season (seasons spec §7): a sheet with a
ragged, hand-made inner edge laid around the stage image, casting a soft
shadow onto it. Each frame is a PaperStyle (the paper's colour and fibres);
the edge, shadow and rim are shared with gold_frame.py. Deterministic: the
same image always gets the same edge and texture."""

import random
from dataclasses import dataclass

from PIL import Image, ImageChops, ImageDraw, ImageFilter

Color = tuple[int, int, int]

SEED = 7
BORDER_RATIO = 0.03  # of the image's short side
_MIN_BORDER = 6
_DECKLE = 0.28  # how far the edge wanders, as a share of the border
_EDGE_STEP = 2  # px between the edge's points
_SHADOW_ALPHA = 0.45
_SHADOW_TINT = (40, 32, 24)


@dataclass(frozen=True)
class PaperStyle:
    paper: Color
    fibre: Color
    fibre_length: int = 6  # px either way
    fibres_per: int = 1500  # one fibre per this many px²


PAPER = PaperStyle(paper=(243, 235, 218), fibre=(214, 202, 178))


def border_for(size: tuple[int, int]) -> int:
    return max(_MIN_BORDER, round(min(size) * BORDER_RATIO))


def deckle_mask(size: tuple[int, int], border: int, rng: random.Random) -> Image.Image:
    """255 where the frame is; its inner edge a slow random walk."""
    w, h = size
    limit = border * _DECKLE
    offset = 0.0

    def wobble() -> float:
        nonlocal offset
        offset = max(-limit, min(limit, offset + rng.uniform(-1.0, 1.0)))
        return offset

    left, top, right, bottom = border, border, w - border, h - border
    step = _EDGE_STEP
    points: list[tuple[float, float]] = [(left, top)]
    points += [(x, top + wobble()) for x in range(left + step, right, step)]
    points.append((right, top))
    points += [(right + wobble(), y) for y in range(top + step, bottom, step)]
    points.append((right, bottom))
    points += [(x, bottom + wobble()) for x in range(right - step, left, -step)]
    points.append((left, bottom))
    points += [(left + wobble(), y) for y in range(bottom - step, top, -step)]
    mask = Image.new("L", size, 255)
    ImageDraw.Draw(mask).polygon(points, fill=0)
    return mask.filter(ImageFilter.GaussianBlur(0.8))


def shadowed(image: Image.Image, mask: Image.Image, border: int) -> Image.Image:
    """`image` with the frame's soft shadow cast just inside its edge."""
    shadow = mask.filter(ImageFilter.GaussianBlur(border * 0.35))
    dark = ImageChops.multiply(image, Image.new("RGB", image.size, _SHADOW_TINT))
    return Image.composite(dark, image, shadow.point(lambda v: round(v * _SHADOW_ALPHA)))


def inner_rim(mask: Image.Image) -> Image.Image:
    """An L mask of the frame's innermost rim, where it meets the image."""
    inner = mask.filter(ImageFilter.MinFilter(3))
    return ImageChops.subtract(mask, inner).point(lambda v: min(255, v * 3))


def _sheet(size: tuple[int, int], style: PaperStyle, rng: random.Random) -> Image.Image:
    """The paper: a fine grain and short fibres."""
    w, h = size
    sheet = Image.new("RGB", size, style.paper)
    noise = Image.frombytes("L", size, rng.randbytes(w * h))
    grain = noise.point(lambda v: max(0, v - 160) * 2 // 3)  # sparse specks, never solid
    sheet = Image.composite(Image.new("RGB", size, style.fibre), sheet, grain)
    draw = ImageDraw.Draw(sheet)
    reach = style.fibre_length
    for _ in range(w * h // style.fibres_per):
        x, y = rng.randrange(w), rng.randrange(h)
        dx, dy = rng.randint(-reach, reach), rng.randint(-reach, reach)
        draw.line((x, y, x + dx, y + dy), fill=style.fibre, width=1)
    return sheet


def framed(image: Image.Image, style: PaperStyle) -> Image.Image:
    """`image` inside a paper frame of `style`, the same size."""
    base = image.convert("RGB")
    rng = random.Random(SEED)  # noqa: S311 - paper texture, seeded for a reproducible look
    border = border_for(base.size)
    mask = deckle_mask(base.size, border, rng)
    return Image.composite(_sheet(base.size, style, rng), shadowed(base, mask, border), mask)


def paper(image: Image.Image) -> Image.Image:
    """The "paper" frame: a cream deckled border with a soft shadow inside it."""
    return framed(image, PAPER)
