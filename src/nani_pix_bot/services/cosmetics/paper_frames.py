"""Paper-like stage frames for Sakura Season (seasons spec §7): a sheet with a
ragged, hand-made inner edge laid around the stage image, casting a soft
shadow onto it. Deterministic: the same image always gets the same edge."""

import random

from PIL import Image, ImageChops, ImageDraw, ImageFilter

PAPER = (243, 235, 218)
PAPER_FIBRE = (214, 202, 178)
BORDER_RATIO = 0.03  # of the image's short side
_MIN_BORDER = 6
_DECKLE = 0.28  # how far the edge wanders, as a share of the border
_EDGE_STEP = 2  # px between the edge's points
_SHADOW_ALPHA = 0.45
_SHADOW_TINT = (40, 32, 24)
_SEED = 7


def _deckle_mask(size: tuple[int, int], border: int, rng: random.Random) -> Image.Image:
    """255 where the paper is: the frame, its inner edge a slow random walk."""
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


def _sheet(size: tuple[int, int], rng: random.Random) -> Image.Image:
    """Cream paper with a fine grain and short fibres."""
    w, h = size
    sheet = Image.new("RGB", size, PAPER)
    noise = Image.frombytes("L", size, rng.randbytes(w * h))
    grain = noise.point(lambda v: max(0, v - 160) * 2 // 3)  # sparse specks, never solid
    sheet = Image.composite(Image.new("RGB", size, PAPER_FIBRE), sheet, grain)
    draw = ImageDraw.Draw(sheet)
    for _ in range(w * h // 1500):
        x, y = rng.randrange(w), rng.randrange(h)
        dx, dy = rng.randint(-6, 6), rng.randint(-6, 6)
        draw.line((x, y, x + dx, y + dy), fill=PAPER_FIBRE, width=1)
    return sheet


def paper(image: Image.Image) -> Image.Image:
    """The "paper" frame: a cream deckled border with a soft shadow inside it."""
    framed = image.convert("RGB")
    rng = random.Random(_SEED)  # noqa: S311 - paper texture, seeded for a reproducible look
    border = max(_MIN_BORDER, round(min(framed.size) * BORDER_RATIO))
    mask = _deckle_mask(framed.size, border, rng)
    shadow = mask.filter(ImageFilter.GaussianBlur(border * 0.35))
    dark = ImageChops.multiply(framed, Image.new("RGB", framed.size, _SHADOW_TINT))
    shaded = Image.composite(dark, framed, shadow.point(lambda v: round(v * _SHADOW_ALPHA)))
    return Image.composite(_sheet(framed.size, rng), shaded, mask)
