"""The Halloween Season stage style: a moonlit night grade over the pixelated
stage image. Indigo night tint and a darkened rim, moonlight streaming in from
the moon at the top left, and art in the two top corners (the moon and a tiny
witch on a broom; bats and a spider). The centre is left untouched.
Deterministic, same size, RGB."""

import math
from collections.abc import Callable
from functools import lru_cache

from loguru import logger
from PIL import Image, ImageChops

from nani_pix_bot.services import sprites

# The radius is 0 at the centre and 1 at the corners; the rim mask is 0 inside
# CLEAR_RADIUS and full at FULL_RADIUS.
CLEAR_RADIUS = 0.42
FULL_RADIUS = 0.97
NIGHT = (60, 64, 150)
NIGHT_TINT = 0.5
EDGE_COLOR = (8, 8, 24)
EDGE_DARK = 0.25
# Moonlight: screened in from the moon (fractions of w and h), strongest at the rim.
MOON = (0.05, 0.07)
BEAM = (190, 210, 255)
BEAM_RADIUS = 0.75
BEAM_STRENGTH = 0.18 * 2.2
# The masks are built on this grid, then stretched up.
GRID = (64, 36)
# Corner art: each sprite is drawn this fraction of the image width wide.
CORNER_WIDTH = 0.325
MIN_CORNER_PX = 4
CORNERS = (("moonlit/top_left.png", False), ("moonlit/top_right.png", True))
# Per-size layers kept: stage images come in a handful of widths at most.
LAYER_CACHE_SIZE = 4

_missing_logged: set[str] = set()


def _smoothstep(x: float) -> float:
    x = min(1.0, max(0.0, x))
    return x * x * (3 - 2 * x)


def _grid(value: Callable[[float, float], float]) -> Image.Image:
    """An "L" image on GRID with value(fx, fy) in 0..1 at each cell centre."""
    gw, gh = GRID
    img = Image.new("L", GRID)
    img.putdata(
        [round(255 * value((x + 0.5) / gw, (y + 0.5) / gh)) for y in range(gh) for x in range(gw)]
    )
    return img


def _rim(fx: float, fy: float) -> float:
    radius = math.hypot(fx - 0.5, fy - 0.5) * math.sqrt(2)
    return _smoothstep((radius - CLEAR_RADIUS) / (FULL_RADIUS - CLEAR_RADIUS))


def _beam(fx: float, fy: float) -> float:
    dist = math.hypot(fx - MOON[0], fy - MOON[1])
    return max(0.0, 1.0 - dist / BEAM_RADIUS) ** 2


@lru_cache(maxsize=LAYER_CACHE_SIZE)
def _layers(size: tuple[int, int]) -> tuple[Image.Image, Image.Image, Image.Image]:
    """The night-tint, rim-darkening and moonlight weights as "L" masks."""
    rim = _grid(_rim)
    moon = ImageChops.multiply(_grid(_beam), rim)

    def up(mask: Image.Image, strength: float) -> Image.Image:
        scaled = mask.point(lambda v: round(v * strength))
        return scaled.resize(size, Image.Resampling.BILINEAR)

    return up(rim, NIGHT_TINT), up(rim, EDGE_DARK), up(moon, BEAM_STRENGTH)


def _grade(rgb: Image.Image) -> Image.Image:
    tint, dark, moon = _layers(rgb.size)
    night = ImageChops.multiply(rgb, Image.new("RGB", rgb.size, NIGHT))
    out = Image.composite(night, rgb, tint)
    out = Image.composite(Image.new("RGB", rgb.size, EDGE_COLOR), out, dark)
    lit = ImageChops.screen(out, Image.new("RGB", rgb.size, BEAM))
    return Image.composite(lit, out, moon)


def _corner(name: str, width: int) -> Image.Image | None:
    """The corner sprite `width` wide, or None when it is too small to show or
    its file is missing (logged once per process: a stage post never breaks)."""
    if width < MIN_CORNER_PX:
        return None
    try:
        art = sprites.sprite(name)
        height = round(width * art.height / art.width)
        if height < MIN_CORNER_PX:
            return None
        return sprites.sprite_height(name, height)
    except OSError:
        if name not in _missing_logged:
            _missing_logged.add(name)
            logger.error("moonlit corner art {name} is missing — posting without it", name=name)
        return None


def moonlit(image: Image.Image) -> Image.Image:
    graded = _grade(image.convert("RGB"))
    w, _ = graded.size
    base = graded.convert("RGBA")
    for name, right in CORNERS:
        art = _corner(name, round(w * CORNER_WIDTH))
        if art is not None:
            base.alpha_composite(art, (w - art.width if right else 0, 0))
    return base.convert("RGB")
