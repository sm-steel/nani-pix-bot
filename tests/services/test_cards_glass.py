import time
from typing import cast

import pytest
from PIL import Image, ImageDraw

from nani_pix_bot.services.cards import CARD_SIZE, glass

W, H = CARD_SIZE


def _rgb(image: Image.Image, xy: tuple[int, int]) -> tuple[int, int, int]:
    return cast(tuple[int, int, int], image.getpixel(xy))


def _close(a: tuple[int, ...], b: tuple[int, ...], slack: int = 3) -> bool:
    return all(abs(x - y) <= slack for x, y in zip(a, b, strict=True))


def test_gradient_runs_from_top_left_to_bottom_right() -> None:
    image = glass.gradient(CARD_SIZE)
    assert image.size == CARD_SIZE
    assert _close(_rgb(image, (0, 0)), glass.GRADIENT_FROM)
    assert _close(_rgb(image, (W - 1, H - 1)), glass.GRADIENT_TO)


def test_gradient_leans_horizontal() -> None:
    """The ramp is mostly left-to-right: the top-right corner is further along than
    the bottom-left one."""
    image = glass.gradient(CARD_SIZE)
    assert _rgb(image, (W - 1, 0))[0] > _rgb(image, (0, H - 1))[0]


@pytest.mark.parametrize(
    ("x", "y", "expected"),
    [(0, 0, 1.0), (W / 2, H / 2, 0.0), (W - 1, H - 1, 0.0), (W - 1, 0, 0.0)],
)
def test_grid_strength_is_full_in_the_corner_and_gone_by_the_middle(x, y, expected) -> None:
    assert glass.grid_strength(x, y, CARD_SIZE) == pytest.approx(expected, abs=0.01)


def test_grid_fades_toward_the_middle() -> None:
    near = glass.grid_strength(60, 40, CARD_SIZE)
    farther = glass.grid_strength(300, 200, CARD_SIZE)
    assert 0 < farther < near < 1


def test_with_grid_brightens_the_corner_and_leaves_the_far_side_alone() -> None:
    flat = Image.new("RGB", CARD_SIZE, (30, 30, 30))
    out = glass.with_grid(flat)
    assert out.size == CARD_SIZE
    assert _rgb(out, (5, 5))[0] > 30  # inside the first block
    assert _rgb(out, (W - 5, H - 5)) == (30, 30, 30)


def _plain_grid(size: tuple[int, int]) -> Image.Image:
    """The grid as it was before it adapted to the picture: white glass everywhere."""
    overlay = Image.new("RGBA", size, (0, 0, 0, 0))
    draw = ImageDraw.Draw(overlay)
    block = glass.GRID_CELL - glass.GRID_GAP
    edge = 2
    for x in range(0, size[0], glass.GRID_CELL):
        for y in range(0, size[1], glass.GRID_CELL):
            k = glass.grid_strength(x + block / 2, y + block / 2, size)
            if k <= 0:
                continue
            draw.rectangle(
                (x, y, x + block - 1, y + block - 1),
                fill=(255, 255, 255, round(glass.GRID_BODY_ALPHA * k)),
            )
            light = (255, 255, 255, round(glass.GRID_EDGE_ALPHA * k))
            draw.rectangle((x, y, x + block - 1, y + edge - 1), fill=light)
            draw.rectangle((x, y, x + edge - 1, y + block - 1), fill=light)
    return overlay


def test_over_a_dark_picture_the_grid_is_the_plain_white_glass() -> None:
    black = Image.new("RGB", CARD_SIZE, (0, 0, 0))
    expected = black.convert("RGBA")
    expected.alpha_composite(_plain_grid(CARD_SIZE))
    assert glass.with_grid(black).tobytes() == expected.convert("RGB").tobytes()


def test_over_a_light_picture_the_blocks_get_a_dark_bottom_edge() -> None:
    white = Image.new("RGB", CARD_SIZE, (255, 255, 255))
    out = glass.with_grid(white)
    block = glass.GRID_CELL - glass.GRID_GAP
    interior = _rgb(out, (block // 2, block // 2))
    bottom = _rgb(out, (block // 2, block - 1))
    right = _rgb(out, (block - 1, block // 2))
    assert sum(bottom) < sum(interior)
    assert sum(right) < sum(interior)
    assert interior < (255, 255, 255)  # the light look's body is a faint dark tint


def test_lightness_ramps_between_the_thresholds() -> None:
    assert glass.lightness(glass.LIGHT_FROM - 10) == 0
    assert glass.lightness(glass.LIGHT_TO + 10) == 1
    assert glass.lightness((glass.LIGHT_FROM + glass.LIGHT_TO) / 2) == pytest.approx(0.5)


def test_the_adaptive_grid_is_fast() -> None:
    picture = Image.linear_gradient("L").resize(CARD_SIZE).convert("RGB")
    glass.with_grid(picture)  # warm up
    start = time.perf_counter()
    glass.with_grid(picture)
    assert time.perf_counter() - start < 0.2


def test_with_grid_leaves_a_gap_between_blocks() -> None:
    flat = Image.new("RGB", CARD_SIZE, (30, 30, 30))
    out = glass.with_grid(flat)
    gap_x = glass.GRID_CELL - 1  # the last column of the first cell is the gap
    assert _rgb(out, (gap_x, 10)) == (30, 30, 30)
