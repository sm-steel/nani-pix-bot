from typing import cast

import pytest
from PIL import Image

from nani_pix_bot.models.enums import RevealEffect
from nani_pix_bot.services.reveal import encode
from nani_pix_bot.services.reveal.effects import EFFECTS, spring_breeze
from tests.services.reveal.effects.conftest import last_frame, mean_abs_diff
from tests.services.reveal.ffprobe_helpers import decode_clean, probe_frames


def _frames(scene) -> list[Image.Image]:
    original, stages = scene
    return [
        Image.frombytes("RGB", original.size, raw) for raw in spring_breeze.frames(original, stages)
    ]


def test_spring_breeze_is_registered() -> None:
    assert EFFECTS[RevealEffect.SPRING_BREEZE] is spring_breeze.render_part1


def test_frames_start_on_the_front_and_end_exactly_on_the_clear_image(scene) -> None:
    original, stages = scene
    frames = _frames(scene)
    assert mean_abs_diff(frames[0], stages[0]) < 1
    assert frames[-1].tobytes() == original.tobytes()


def test_the_clear_image_spreads_from_the_left() -> None:
    """Mid-sweep, the left edge is already clear (red) while the right is still the
    pixelated front (blue): a scene where the two differ everywhere."""
    size = (320, 180)
    clear, front = Image.new("RGB", size, (220, 30, 30)), Image.new("RGB", size, (30, 30, 220))
    raw = list(spring_breeze.frames(clear, [front]))
    middle = Image.frombytes("RGB", size, raw[round(spring_breeze.SWEEP_S * encode.FPS / 2)])
    w, h = size
    left = middle.crop((0, 0, w // 8, h)).resize((1, 1), Image.Resampling.BOX)
    right = middle.crop((w - w // 8, 0, w, h)).resize((1, 1), Image.Resampling.BOX)
    lr, _lg, lb = cast(tuple[int, int, int], left.getpixel((0, 0)))
    rr, _rg, rb = cast(tuple[int, int, int], right.getpixel((0, 0)))
    assert lr > lb  # mostly clear
    assert rb > rr  # mostly still pixelated


def test_petals_blush_pink_as_they_fly(scene) -> None:
    """Some frame carries pink petal pixels that neither the clear nor the pixelated image has."""
    original, stages = scene

    def pinkish(image: Image.Image) -> int:
        data = image.tobytes()
        return sum(
            1
            for i in range(0, len(data), 3)
            if data[i] > 200 and data[i] > data[i + 1] + 30 and data[i + 2] > data[i + 1]
        )

    baseline = max(pinkish(original), pinkish(stages[0]))
    assert max(pinkish(f) for f in _frames(scene)) > baseline + 20


def test_spring_breeze_is_deterministic(scene) -> None:
    original, stages = scene
    assert list(spring_breeze.frames(original, stages)) == list(
        spring_breeze.frames(original, stages)
    )


@pytest.mark.ffmpeg
def test_spring_breeze_part1_is_valid_and_ends_clear(scene) -> None:
    original, stages = scene
    mp4 = spring_breeze.render_part1(original, stages)
    assert decode_clean(mp4)
    assert probe_frames(mp4) > round(encode.HOLD_START * encode.FPS)
    assert mean_abs_diff(last_frame(mp4, original.size), original) < 6
