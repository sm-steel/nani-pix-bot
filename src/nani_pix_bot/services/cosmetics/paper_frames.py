"""Paper-like stage frames for Sakura Season (seasons spec §7): a sheet with a
ragged, hand-made inner edge laid around the stage image, casting a soft
shadow onto it. Each frame is a PaperStyle (the paper's colour and fibres);
the edge, shadow and rim are shared with gold_frame.py. Deterministic: the
same image always gets the same edge and texture."""

import math
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
    petals_per: int = 0  # one pressed petal per this many px² of border band; 0 = none
    unryu_per: int = 0  # one long curly white fibre per this many px² of band; 0 = none


PAPER = PaperStyle(paper=(243, 235, 218), fibre=(214, 202, 178))
WASHI = PaperStyle(
    paper=(244, 206, 216),
    fibre=(232, 186, 198),
    fibre_length=10,
    fibres_per=900,
    petals_per=800,
    unryu_per=260,
)
_PRESSED_PETAL = (226, 104, 140, 200)  # sakura pink, slightly see-through
_PRESSED_VEIN = (190, 70, 110, 170)
_UNRYU = (255, 250, 252, 170)  # the white "cloud dragon" fibres
_UNRYU_STEPS = 14
_PETAL_SS = 3


def petal_outline(
    base: tuple[float, float], length: float, width: float, angle: float
) -> list[tuple[float, float]]:
    """One sakura petal from `base` along `angle`: widening, rounded, with the
    notch at its tip."""
    half_lobe, notch_depth = 0.38, 0.84
    side = [
        (length * t, width * math.sin(math.pi * t) ** 0.6)
        for t in (0.04 + 0.88 * i / 14 for i in range(15))
    ]
    local = [(u, -v) for u, v in side]
    local += [(length, -width * half_lobe), (length * notch_depth, 0.0)]
    local += [(length, width * half_lobe)]
    local += [(u, v) for u, v in reversed(side)]
    cos, sin = math.cos(angle), math.sin(angle)
    x, y = base
    return [(x + u * cos - v * sin, y + u * sin + v * cos) for u, v in local]


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


def _band_point(size: tuple[int, int], border: int, rng: random.Random) -> tuple[float, float]:
    """A random point in the border band, any of the four sides."""
    w, h = size
    side = rng.randrange(4)
    along = rng.uniform(0, w if side < 2 else h)
    depth = rng.uniform(0, border)
    return {
        0: (along, depth),
        1: (along, h - depth),
        2: (depth, along),
        3: (w - depth, along),
    }[side]


def _unryu(
    draw: ImageDraw.ImageDraw, size: tuple[int, int], count: int, border: int, rng: random.Random
) -> None:
    """Long white fibres that curl as they go: a slowly turning random walk."""
    for _ in range(count):
        x, y = _band_point(size, border, rng)
        heading = rng.uniform(0, math.tau)
        step = border * rng.uniform(0.25, 0.45) * _PETAL_SS
        points = [(x * _PETAL_SS, y * _PETAL_SS)]
        for _ in range(_UNRYU_STEPS):
            heading += rng.uniform(-0.6, 0.6)
            px, py = points[-1]
            points.append((px + math.cos(heading) * step, py + math.sin(heading) * step))
        draw.line(points, fill=_UNRYU, width=_PETAL_SS, joint="curve")


def _pressed_petals(
    sheet: Image.Image, style: PaperStyle, border: int, rng: random.Random
) -> Image.Image:
    """White unryū fibres and sakura petals pressed into the paper's border band."""
    w, h = sheet.size
    layer = Image.new("RGBA", (w * _PETAL_SS, h * _PETAL_SS), (0, 0, 0, 0))
    draw = ImageDraw.Draw(layer)
    band = w * h - (w - 2 * border) * (h - 2 * border)
    if style.unryu_per:
        _unryu(draw, sheet.size, band // style.unryu_per, border, rng)
    for _ in range(band // style.petals_per):
        x, y = _band_point(sheet.size, border, rng)
        length = border * rng.uniform(1.0, 1.6) * _PETAL_SS
        angle = rng.uniform(0, math.tau)
        base = (x * _PETAL_SS, y * _PETAL_SS)
        draw.polygon(petal_outline(base, length, length * 0.3, angle), fill=_PRESSED_PETAL)
        tip = (base[0] + math.cos(angle) * length * 0.7, base[1] + math.sin(angle) * length * 0.7)
        draw.line((base, tip), fill=_PRESSED_VEIN, width=_PETAL_SS)
    petals = layer.resize(sheet.size, Image.Resampling.LANCZOS)
    out = sheet.convert("RGBA")
    out.alpha_composite(petals)
    return out.convert("RGB")


def framed(image: Image.Image, style: PaperStyle) -> Image.Image:
    """`image` inside a paper frame of `style`, the same size."""
    base = image.convert("RGB")
    rng = random.Random(SEED)  # noqa: S311 - paper texture, seeded for a reproducible look
    border = border_for(base.size)
    mask = deckle_mask(base.size, border, rng)
    sheet = _sheet(base.size, style, rng)
    if style.petals_per:
        sheet = _pressed_petals(sheet, style, border, rng)
    return Image.composite(sheet, shadowed(base, mask, border), mask)


def paper(image: Image.Image) -> Image.Image:
    """The "paper" frame: a cream deckled border with a soft shadow inside it."""
    return framed(image, PAPER)


def washi(image: Image.Image) -> Image.Image:
    """The "washi" frame: pink-white washi with long fibres and pressed sakura petals."""
    return framed(image, WASHI)
