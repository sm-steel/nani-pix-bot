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


@lru_cache(maxsize=2)
def _grid(size: tuple[int, int]) -> Image.Image:
    overlay = Image.new("RGBA", size, (0, 0, 0, 0))
    draw = ImageDraw.Draw(overlay)
    block = GRID_CELL - GRID_GAP
    for x in range(0, size[0], GRID_CELL):
        for y in range(0, size[1], GRID_CELL):
            k = grid_strength(x + block / 2, y + block / 2, size)
            if k <= 0:
                continue
            body = (255, 255, 255, round(GRID_BODY_ALPHA * k))
            edge = (255, 255, 255, round(GRID_EDGE_ALPHA * k))
            draw.rectangle((x, y, x + block - 1, y + block - 1), fill=body)
            draw.rectangle((x, y, x + block - 1, y + _GRID_EDGE - 1), fill=edge)
            draw.rectangle((x, y, x + _GRID_EDGE - 1, y + block - 1), fill=edge)
    return overlay


def with_grid(image: Image.Image) -> Image.Image:
    """`image` with the glass grid laid over it."""
    out = image.convert("RGBA")
    out.alpha_composite(_grid(out.size))
    return out.convert("RGB")
