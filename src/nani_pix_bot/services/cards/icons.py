"""Icons drawn on the cards (spec §7), because Noto Sans can't draw emoji:
a gold trophy for achievement points, a glowing star for a period
score, the rarity medal and the currency diamond. Each is painted at 4x and
shrunk with LANCZOS, so edges are smooth."""

import math

from PIL import Image, ImageDraw, ImageFilter

_SS = 4
_GOLD = (240, 190, 40)
_GOLD_LIGHT = (255, 226, 120)
_GOLD_DARK = (190, 135, 20)
_STAR_FILL = (255, 247, 214)
_STAR_HALO = (255, 205, 70)
_STAR_POINTS = 5


def _canvas(size: int) -> tuple[Image.Image, ImageDraw.ImageDraw, float]:
    big = size * _SS
    image = Image.new("RGBA", (big, big), (0, 0, 0, 0))
    return image, ImageDraw.Draw(image), big / 32  # the drawings below use a 32-unit grid


def trophy(size: int = 32) -> Image.Image:
    """A gold cup with two handles, a stem and a base, on a 32-unit grid."""
    image, draw, u = _canvas(size)
    for box, start, end in (((1, 4, 12, 15), 90, 270), ((20, 4, 31, 15), 270, 90)):
        draw.arc([v * u for v in box], start, end, fill=_GOLD_DARK, width=round(2.2 * u))
    draw.rectangle((8 * u, 3 * u, 24 * u, 12 * u), fill=_GOLD)
    draw.pieslice((8 * u, 4 * u, 24 * u, 20 * u), 0, 180, fill=_GOLD)
    draw.rectangle((11 * u, 4 * u, 13 * u, 14 * u), fill=_GOLD_LIGHT)
    draw.rectangle((14 * u, 20 * u, 18 * u, 25 * u), fill=_GOLD_DARK)
    draw.rounded_rectangle((9 * u, 25 * u, 23 * u, 29 * u), radius=1.5 * u, fill=_GOLD)
    return image.resize((size, size), Image.Resampling.LANCZOS)


def _star_outline(centre: float, outer: float, inner: float) -> list[tuple[float, float]]:
    points = []
    for i in range(2 * _STAR_POINTS):
        radius = outer if i % 2 == 0 else inner
        angle = math.radians(-90 + i * 180 / _STAR_POINTS)
        points.append((centre + radius * math.cos(angle), centre + radius * math.sin(angle)))
    return points


def star(size: int = 44) -> Image.Image:
    """A white-gold five-point star with a soft gold halo; the star itself
    fills about two thirds of the box, the rest is room for the glow."""
    image, _, u = _canvas(size)
    outline = _star_outline(16 * u, 10.5 * u, 4.4 * u)
    halo = Image.new("L", image.size, 0)
    ImageDraw.Draw(halo).polygon(outline, fill=255)
    halo = halo.filter(ImageFilter.GaussianBlur(3.2 * u)).point(lambda v: round(v * 0.9))
    glow = Image.new("RGBA", image.size, _STAR_HALO)
    glow.putalpha(halo)
    image = Image.alpha_composite(image, glow)
    ImageDraw.Draw(image).polygon(outline, fill=_STAR_FILL, outline=_GOLD, width=round(0.8 * u))
    return image.resize((size, size), Image.Resampling.LANCZOS)


Color = tuple[int, int, int]


def medal(size: int, color: Color, hole: Color) -> Image.Image:
    """A round rarity medal: a `color` disc with a thin `hole` ring inside it."""
    image, draw, u = _canvas(size)
    draw.ellipse((0, 0, 32 * u, 32 * u), fill=color)
    inset = 5.2 * u
    draw.ellipse((inset, inset, 32 * u - inset, 32 * u - inset), outline=hole, width=round(2.7 * u))
    return image.resize((size, size), Image.Resampling.LANCZOS)


def diamond(size: int, color: Color) -> Image.Image:
    """A flat `color` diamond filling the box."""
    image, draw, u = _canvas(size)
    draw.polygon([(16 * u, 0), (32 * u, 16 * u), (16 * u, 32 * u), (0, 16 * u)], fill=color)
    return image.resize((size, size), Image.Resampling.LANCZOS)
