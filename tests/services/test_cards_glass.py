from typing import cast

import pytest
from PIL import Image

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


def test_with_grid_leaves_a_gap_between_blocks() -> None:
    flat = Image.new("RGB", CARD_SIZE, (30, 30, 30))
    out = glass.with_grid(flat)
    gap_x = glass.GRID_CELL - 1  # the last column of the first cell is the gap
    assert _rgb(out, (gap_x, 10)) == (30, 30, 30)
