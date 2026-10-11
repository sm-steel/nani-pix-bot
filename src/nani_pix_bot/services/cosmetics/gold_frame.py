"""The "gold_washi" stage frame (Sakura Season, seasons spec §7): a band of
gold leaf around the stage image, shining like metal, with the square seams
of the leaf sheets, an embossed seigaiha wave pattern, a sakura blossom on
every corner, and the paper frames' ragged inner edge and soft shadow.
Drawn supersampled where it has curves; deterministic."""

import math
import random

from PIL import Image, ImageDraw, ImageFilter

from nani_pix_bot.services.cosmetics import paper_frames

Color = tuple[int, int, int]

# Metallic sweep: dark → bright → highlight → bright → dark, repeating diagonally.
_SHEEN: tuple[Color, ...] = (
    (96, 62, 8),
    (176, 126, 28),
    (238, 196, 84),
    (255, 248, 210),
    (240, 198, 86),
    (150, 104, 20),
    (206, 158, 50),
)
_SHEEN_PERIOD = 0.3  # one sweep per this share of the image's diagonal
_SEAM = (150, 108, 24)
_SEAM_ALPHA = 0.35
_WAVE = (255, 236, 160)
_WAVE_ALPHA = 70
_RIM = (110, 74, 12)
_PETAL = (250, 214, 110)
_PETAL_EDGE = (120, 80, 14)
_PETAL_CENTRE = (196, 60, 70)
_BLOSSOM_RATIO = 2.6  # blossom diameter in borders
_SS = 4  # supersampling for the blossoms


def _lerp(a: Color, b: Color, t: float) -> Color:
    r, g, bl = (round(x + (y - x) * t) for x, y in zip(a, b, strict=True))
    return (r, g, bl)


def _sheen(size: tuple[int, int]) -> Image.Image:
    """The metal: a diagonal gradient cycling through _SHEEN."""
    w, h = size
    period = math.hypot(w, h) * _SHEEN_PERIOD
    stops = len(_SHEEN)
    ramp = Image.new("RGB", (stops * 64, 1))
    for i in range(ramp.width):
        pos = i / ramp.width * stops
        k = int(pos)
        ramp.putpixel((i, 0), _lerp(_SHEEN[k], _SHEEN[(k + 1) % stops], pos - k))
    diagonal = ramp.resize((round(period), 1), Image.Resampling.BILINEAR)
    span = w + h
    strip = Image.new("RGB", (span, 1))
    for x in range(0, span, diagonal.width):
        strip.paste(diagonal, (x, 0))
    # Shear the horizontal strip into diagonal bands: pixel (x, y) takes strip[x + y].
    band = strip.resize((span, h), Image.Resampling.NEAREST)
    return band.transform(size, Image.Transform.AFFINE, (1, 1, 0, 0, 1, 0))


def _seams(sheet: Image.Image, border: int, rng: random.Random) -> Image.Image:
    """Faint seams where the square gold-leaf sheets meet, slightly irregular."""
    lines = Image.new("L", sheet.size, 0)
    draw = ImageDraw.Draw(lines)
    w, h = sheet.size
    step = border * 3
    for x in range(0, w, step):
        x0 = x + rng.randint(-2, 2)
        draw.line((x0, 0, x0, h), fill=255, width=1)
    for y in range(0, h, step):
        y0 = y + rng.randint(-2, 2)
        draw.line((0, y0, w, y0), fill=255, width=1)
    faint = lines.point(lambda v: round(v * _SEAM_ALPHA))
    return Image.composite(Image.new("RGB", sheet.size, _SEAM), sheet, faint)


def _seigaiha(size: tuple[int, int], border: int) -> Image.Image:
    """An L mask of the overlapping-wave pattern: rows of concentric arcs."""
    w, h = size
    radius = max(4, border // 2)
    mask = Image.new("L", (w * 2, h * 2), 0)
    draw = ImageDraw.Draw(mask)
    r2 = radius * 2
    for row, y in enumerate(range(0, h * 2 + r2, radius)):
        shift = radius if row % 2 else 0
        for x in range(-r2 + shift, w * 2 + r2, r2):
            for ring in (1.0, 0.7, 0.4):
                rr = r2 * ring
                draw.arc((x - rr, y - rr, x + rr, y + rr), 180, 360, fill=255, width=2)
    return mask.resize(size, Image.Resampling.LANCZOS)


def _petal(centre: float, length: float, width: float, angle: float) -> list[tuple[float, float]]:
    """One sakura petal pointing along `angle`: widening from the centre, rounded,
    with the notch at its tip."""
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
    return [(centre + u * cos - v * sin, centre + u * sin + v * cos) for u, v in local]


def _blossom(diameter: int, rotation: float) -> Image.Image:
    """A gold five-petal sakura blossom with a dark outline and a red centre."""
    big = diameter * _SS
    image = Image.new("RGBA", (big, big), (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)
    centre = big / 2
    outline = max(2, big // 36)
    for i in range(5):
        angle = rotation + i * math.tau / 5
        petal = _petal(centre, big * 0.47, big * 0.2, angle)
        draw.polygon(petal, fill=_PETAL, outline=_PETAL_EDGE, width=outline)
    core = big * 0.08
    draw.ellipse((centre - core, centre - core, centre + core, centre + core), fill=_PETAL_CENTRE)
    return image.resize((diameter, diameter), Image.Resampling.LANCZOS)


def _with_blossoms(image: Image.Image, border: int) -> Image.Image:
    out = image.convert("RGBA")
    w, h = out.size
    diameter = round(border * _BLOSSOM_RATIO)
    half = diameter // 2
    inset = half + max(2, border // 6)  # whole blossom on the card, over the corner
    corners = ((inset, inset), (w - inset, inset), (inset, h - inset), (w - inset, h - inset))
    for i, (x, y) in enumerate(corners):
        blossom = _blossom(diameter, rotation=-math.pi / 2 + i * 0.6)
        shadow = Image.new("RGBA", blossom.size, (30, 20, 5, 0))
        shadow.putalpha(blossom.getchannel("A").point(lambda v: v * 3 // 5))
        shadow = shadow.filter(ImageFilter.GaussianBlur(max(1, border // 6)))
        offset = max(1, border // 8)
        out.alpha_composite(shadow, (x - half + offset, y - half + offset))
        out.alpha_composite(blossom, (x - half, y - half))
    return out.convert("RGB")


def gold_washi(image: Image.Image) -> Image.Image:
    base = image.convert("RGB")
    rng = random.Random(paper_frames.SEED)  # noqa: S311 - leaf seams, seeded for a reproducible look
    border = paper_frames.border_for(base.size)
    mask = paper_frames.deckle_mask(base.size, border, rng)
    shaded = paper_frames.shadowed(base, mask, border)
    gold = _seams(_sheen(base.size), border, rng)
    waves = _seigaiha(base.size, border).point(lambda v: v * _WAVE_ALPHA // 255)
    gold = Image.composite(Image.new("RGB", base.size, _WAVE), gold, waves)
    out = Image.composite(gold, shaded, mask)
    rim = paper_frames.inner_rim(mask)
    out = Image.composite(Image.new("RGB", base.size, _RIM), out, rim)
    return _with_blossoms(out, border)
