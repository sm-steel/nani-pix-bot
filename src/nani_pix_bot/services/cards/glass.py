"""The profile card's backdrop: a diagonal gradient (the default when there's no
banner) and a grid of faint glass pixels, strongest in the top-left corner and
gone by the card's middle, laid over whatever is behind the card."""

import math
from functools import lru_cache

from PIL import Image, ImageDraw

GRADIENT_FROM = (20, 22, 40)  # top-left
GRADIENT_TO = (72, 38, 92)  # bottom-right
_GRADIENT_X_SHARE = 0.7  # the rest of the ramp runs top to bottom
GRID_CELL = 40  # pitch of the grid
GRID_GAP = 2  # clear space between two blocks
GRID_BODY_ALPHA = 24  # block fill, in the top-left corner
GRID_EDGE_ALPHA = 56  # top and left highlight, in the top-left corner
_GRID_EDGE = 2
# The light look, for blocks over a bright backdrop (a pale sky, blossoms): white glass
# would vanish there, so the block gets a faint dark tint and a two-tone bevel.
LIGHT_LOOK_BODY = (20, 20, 40)
LIGHT_LOOK_BODY_ALPHA = 26
LIGHT_LOOK_EDGE_ALPHA = 150  # top and left highlight, white
LIGHT_LOOK_SHADE_ALPHA = 44  # bottom and right edge, black
LIGHT_FROM, LIGHT_TO = 110, 190  # mean luminance where the switch starts and ends


@lru_cache(maxsize=2)
def gradient(size: tuple[int, int]) -> Image.Image:
    """GRADIENT_FROM in the top-left corner to GRADIENT_TO in the bottom-right."""
    width, height = size
    down = Image.linear_gradient("L")  # 0 at the top, 255 at the bottom
    across = down.transpose(Image.Transpose.TRANSPOSE)  # 0 at the left, 255 at the right
    ramp = Image.blend(
        across.resize(size, Image.Resampling.BILINEAR),
        down.resize(size, Image.Resampling.BILINEAR),
        1 - _GRADIENT_X_SHARE,
    )
    return Image.composite(
        Image.new("RGB", (width, height), GRADIENT_TO),
        Image.new("RGB", (width, height), GRADIENT_FROM),
        ramp,
    )


def grid_strength(x: float, y: float, size: tuple[int, int]) -> float:
    """1 at the top-left corner, falling to 0 at the card's centre and beyond."""
    u, v = x / size[0], y / size[1]
    return max(0.0, 1 - math.sqrt((u * u + v * v) / 0.5))


def lightness(luminance: float) -> float:
    """How far a block's backdrop is into the light look: 0 at LIGHT_FROM or darker,
    1 at LIGHT_TO or lighter, linear in between."""
    return min(1.0, max(0.0, (luminance - LIGHT_FROM) / (LIGHT_TO - LIGHT_FROM)))


def _mix(dark: float, light: float, t: float) -> float:
    return dark + (light - dark) * t


def _cell_luminance(image: Image.Image) -> Image.Image:
    """The mean luminance under every grid cell, one pixel per cell (padded with black
    past the image's edge)."""
    columns = -(-image.width // GRID_CELL)
    rows = -(-image.height // GRID_CELL)
    gray = image.convert("L").crop((0, 0, columns * GRID_CELL, rows * GRID_CELL))
    return gray.reduce(GRID_CELL)


def _block(draw: ImageDraw.ImageDraw, x: int, y: int, k: float, t: float) -> None:
    """One glass block at strength `k`, its look blended by the backdrop's lightness `t`."""
    block = GRID_CELL - GRID_GAP
    body = tuple(round(_mix(255, dark, t)) for dark in LIGHT_LOOK_BODY)
    body_alpha = round(_mix(GRID_BODY_ALPHA, LIGHT_LOOK_BODY_ALPHA, t) * k)
    highlight = (255, 255, 255, round(_mix(GRID_EDGE_ALPHA, LIGHT_LOOK_EDGE_ALPHA, t) * k))
    shade_alpha = round(LIGHT_LOOK_SHADE_ALPHA * t * k)
    right, bottom = x + block - 1, y + block - 1
    draw.rectangle((x, y, right, bottom), fill=(*body, body_alpha))
    if shade_alpha:
        shade = (0, 0, 0, shade_alpha)
        draw.rectangle((x, bottom - _GRID_EDGE + 1, right, bottom), fill=shade)
        draw.rectangle((right - _GRID_EDGE + 1, y, right, bottom), fill=shade)
    draw.rectangle((x, y, right, y + _GRID_EDGE - 1), fill=highlight)
    draw.rectangle((x, y, x + _GRID_EDGE - 1, bottom), fill=highlight)


def with_grid(image: Image.Image) -> Image.Image:
    """`image` with the glass grid laid over it. Each block reads the brightness
    under it: white glass over a dark backdrop, a faint dark tint with a white
    top-left and a dark bottom-right bevel over a light one, blended in between."""
    out = image.convert("RGBA")
    luminance = _cell_luminance(out)
    means, columns = luminance.tobytes(), luminance.width
    overlay = Image.new("RGBA", out.size, (0, 0, 0, 0))
    draw = ImageDraw.Draw(overlay)
    block = GRID_CELL - GRID_GAP
    for x in range(0, out.width, GRID_CELL):
        for y in range(0, out.height, GRID_CELL):
            k = grid_strength(x + block / 2, y + block / 2, out.size)
            if k > 0:
                mean = means[(y // GRID_CELL) * columns + x // GRID_CELL]
                _block(draw, x, y, k, lightness(mean))
    out.alpha_composite(overlay)
    return out.convert("RGB")
