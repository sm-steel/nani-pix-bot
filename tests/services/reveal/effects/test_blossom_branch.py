import random
from typing import cast

import pytest
from PIL import Image

from nani_pix_bot.models.enums import RevealEffect
from nani_pix_bot.services.reveal import encode
from nani_pix_bot.services.reveal.effects import EFFECTS, blossom_branch
from tests.services.reveal.effects.conftest import last_frame, mean_abs_diff
from tests.services.reveal.ffprobe_helpers import decode_clean, probe_frames

SIZE = (320, 180)


def _paths() -> list:
    return blossom_branch._paths(SIZE, random.Random(blossom_branch.SEED))  # noqa: S311 - test


def _frames(clear: Image.Image, front: Image.Image) -> list[Image.Image]:
    return [Image.frombytes("RGB", SIZE, raw) for raw in blossom_branch.frames(clear, [front])]


@pytest.fixture(scope="module")
def red_blue() -> list[Image.Image]:
    """Clear = red, pixelated = blue: the two differ everywhere."""
    return _frames(Image.new("RGB", SIZE, (220, 30, 30)), Image.new("RGB", SIZE, (30, 30, 220)))


def test_blossom_branch_is_registered() -> None:
    assert EFFECTS[RevealEffect.BLOSSOM_BRANCH] is blossom_branch.render_part1


def test_frames_start_on_the_front_and_end_exactly_on_the_clear_image(scene) -> None:
    original, stages = scene
    frames = [
        Image.frombytes("RGB", original.size, raw)
        for raw in blossom_branch.frames(original, stages)
    ]
    assert mean_abs_diff(frames[0], stages[0]) < 1
    assert frames[-1].tobytes() == original.tobytes()


def test_the_main_branch_crosses_the_image_and_stays_inside_it_vertically() -> None:
    trunk = _paths()[0]
    xs = [x for x, _y in trunk.points]
    ys = [y for _x, y in trunk.points]
    w, h = SIZE
    assert min(xs) < 0 < w < max(xs)
    assert min(ys) > 0
    assert max(ys) < h


def test_the_eraser_starts_when_the_main_branch_is_half_drawn() -> None:
    trunk = _paths()[0]
    half = blossom_branch.GROW_S / 2
    assert pytest.approx(half) == blossom_branch.ERASE_DELAY
    erasing = trunk.drawn(half + 0.05 - blossom_branch.ERASE_DELAY)
    assert erasing > 0  # just after half-drawn, the base is going
    assert trunk.drawn(half + 0.05) > len(trunk.points) // 2


def test_the_branch_is_erased_from_its_base_first() -> None:
    """Mid-erase, the bark is gone near the base and still there toward the tip."""
    paths = _paths()
    trunk = paths[0]
    t = blossom_branch.ERASE_DELAY + blossom_branch.GROW_S * 0.45
    mask = blossom_branch._bark_mask(paths, t, SIZE)
    base = trunk.points[2]
    tip_side = trunk.points[trunk.drawn(t) - 3]
    assert mask.getpixel((round(base[0]), round(base[1]))) == 0
    assert mask.getpixel((round(tip_side[0]), round(tip_side[1]))) == 255


def test_blossoms_open_then_shrink_away_as_the_eraser_passes() -> None:
    at, appears = 0.5, 0.55
    grow = blossom_branch.OPEN_S
    ease = blossom_branch.ease_out_back
    assert blossom_branch._scale(0.5, appears, at, grow, ease) == 0  # not yet open
    assert blossom_branch._scale(appears + grow, appears, at, grow, ease) == pytest.approx(1)
    gone = at + blossom_branch.ERASE_DELAY + grow
    assert blossom_branch._scale(gone, appears, at, grow, ease) == pytest.approx(0)


def test_the_clear_image_spreads_out_from_the_blossoms(red_blue) -> None:
    def redness(frame: Image.Image) -> int:
        r, _g, b = cast(
            tuple[int, int, int], frame.resize((1, 1), Image.Resampling.BOX).getpixel((0, 0))
        )
        return r - b

    early, middle = red_blue[5], red_blue[len(red_blue) // 2]
    assert redness(early) < 0 < redness(middle)


def test_the_last_frame_has_no_branch_left(red_blue) -> None:
    assert red_blue[-1].tobytes() == Image.new("RGB", SIZE, (220, 30, 30)).tobytes()
    assert red_blue[-2].getcolors() is not None  # one solid frame before it: nothing drawn
    colours = cast(list[tuple[int, tuple[int, int, int]]], red_blue[-2].getcolors())
    assert len(colours) == 1


def test_blossom_branch_is_deterministic(scene) -> None:
    original, stages = scene
    assert list(blossom_branch.frames(original, stages)) == list(
        blossom_branch.frames(original, stages)
    )


@pytest.mark.ffmpeg
def test_blossom_branch_part1_is_valid_and_ends_clear(scene) -> None:
    original, stages = scene
    mp4 = blossom_branch.render_part1(original, stages)
    assert decode_clean(mp4)
    assert probe_frames(mp4) > round(encode.HOLD_START * encode.FPS)
    assert mean_abs_diff(last_frame(mp4, original.size), original) < 6
