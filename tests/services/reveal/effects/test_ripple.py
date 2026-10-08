import pytest

from nani_pix_bot.models.enums import RevealEffect
from nani_pix_bot.services.reveal import encode
from nani_pix_bot.services.reveal.effects import EFFECTS, ripple
from tests.services.reveal.effects.conftest import last_frame, mean_abs_diff
from tests.services.reveal.ffprobe_helpers import decode_clean, decode_frames, probe_frames


@pytest.mark.ffmpeg
def test_ripple_part1_is_valid_and_ends_clear(scene) -> None:
    original, stages = scene
    mp4 = ripple.render_part1(original, stages)
    assert decode_clean(mp4)
    assert probe_frames(mp4) > round(encode.HOLD_START * encode.FPS)
    assert mean_abs_diff(last_frame(mp4, original.size), original) < 6


def test_ripple_is_registered() -> None:
    assert EFFECTS[RevealEffect.RIPPLE] is ripple.render_part1


def test_frames_are_yuv420p_planes_ending_on_the_clear_image(scene) -> None:
    original, stages = scene
    frames = list(ripple.render_yuv(original, stages))
    # opening still + 4 x (12 ripple + 8 hold) + 16 final ripple + 9 flash, at 30 fps
    assert len(frames) == 1 + 4 * (12 + 8) + 16 + 9
    assert all(len(f) == 3 for f in frames)
    assert [len(p) for p in frames[0]] == [320 * 180, 160 * 90, 160 * 90]
    assert frames[0] != frames[-1]


def test_a_mid_ripple_frame_differs_from_both_neighbouring_stages(scene) -> None:
    original, stages = scene
    frames = list(ripple.render_yuv(original, stages))
    # frame 1 is the first ripple step of stage 0 -> 1: partly the new stage, mostly the old
    assert frames[1][0] != frames[0][0]
    assert frames[1][0] != frames[12][0]


def test_interesting_point_prefers_the_detailed_cell() -> None:
    from PIL import Image, ImageDraw

    img = Image.new("RGB", (320, 180), (100, 100, 100))
    d = ImageDraw.Draw(img)
    for x in range(200, 240, 4):
        d.line((x, 60, x, 120), fill=(255, 255, 255), width=2)
    x, y = ripple.interesting_point(img)
    assert 190 <= x <= 250
    assert 50 <= y <= 130


def test_ripple_frame_arithmetic_is_exact(scene) -> None:
    original, stages = scene
    mp4 = ripple.render_part1(original, stages)
    hold = round(encode.HOLD_START * encode.FPS)  # tpad's cloned opening frames
    timeline = ripple._timeline(len(stages), encode.FPS)
    assert timeline[-1].kind == "flash"
    assert timeline[-1].a == ripple.FLASH_FRAMES  # the decayed flash is the clear image, once
    assert probe_frames(mp4) == hold + sum(step.repeat for step in timeline)
    img = decode_frames(mp4, original.size)
    assert len(img) == probe_frames(mp4)
    assert mean_abs_diff(img[-1], img[-2]) > 0.5  # the clear frame appears exactly once
    assert mean_abs_diff(img[-1], original) < 6
