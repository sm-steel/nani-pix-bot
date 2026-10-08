import math

import pytest
from PIL import Image

from nani_pix_bot.models.enums import RevealEffect
from nani_pix_bot.services.reveal import encode
from nani_pix_bot.services.reveal.effects import EFFECTS, glitch
from tests.services.reveal.effects.conftest import last_frame, mean_abs_diff
from tests.services.reveal.ffprobe_helpers import decode_clean, decode_frames, probe_frames


@pytest.mark.ffmpeg
def test_glitch_part1_is_valid_and_ends_clear(scene) -> None:
    original, stages = scene
    mp4 = glitch.render_part1(original, stages)
    assert decode_clean(mp4)
    assert probe_frames(mp4) > round(encode.HOLD_START * encode.FPS)
    assert mean_abs_diff(last_frame(mp4, original.size), original) < 6


@pytest.mark.ffmpeg
def test_glitch_frame_arithmetic_is_exact(scene) -> None:
    original, stages = scene
    mp4 = glitch.render_part1(original, stages)
    k = encode.FPS // glitch.GLITCH_FPS
    hold_in = math.ceil(encode.HOLD_START * glitch.GLITCH_FPS)
    n_in = len(glitch.plan(len(stages), glitch.GLITCH_FPS, glitch.INTRO_S)) + 2
    assert probe_frames(mp4) == k * (hold_in + n_in) - (k - 1)
    img = decode_frames(mp4, original.size)
    assert len(img) == probe_frames(mp4)
    assert mean_abs_diff(img[-1], img[-2]) > 0.5  # the clear frame appears exactly once
    hold = k * hold_in + k  # padded clones + the clean first frame, k outputs each
    assert all(mean_abs_diff(img[0], f) < 0.5 for f in img[:hold])  # static coarse image
    assert mean_abs_diff(img[0], img[hold]) > 0.5  # then it starts glitching


def test_glitch_is_registered() -> None:
    assert EFFECTS[RevealEffect.GLITCH] is glitch.render_part1


def test_plan_steps_through_the_stages_and_ends_on_the_clear_image() -> None:
    steps = glitch.plan(5, 15, 0.3)
    # 4 intro frames + 36 stepping frames + 4 final-burst frames
    assert len(steps) == 4 + 36 + 4
    assert [s for s, _, _ in steps[:4]] == [0] * 4
    assert [s for s, _, _ in steps[-4:]] == [5] * 4
    assert [a for _, a, _ in steps[-4:]] == [1.0, 0.8, 0.5, 0.2]
    stages = [s for s, _, _ in steps[4:-4]]
    assert stages == sorted(stages)


def test_frames_are_gbrp_planes_clean_first_and_last(scene) -> None:
    original, stages = scene
    frames = list(glitch.frames(original, stages))
    assert len(frames) == 1 + len(glitch.plan(5, 15, 0.3)) + 1
    assert all(len(f) == 3 * 320 * 180 for f in frames)
    g, b, r = (original.getchannel(c).tobytes() for c in "GBR")
    assert bytes(frames[-1]) == g + b + r
    assert frames[0] != frames[-1]
    assert frames[1] != frames[0]  # the intro already glitches


def test_a_calm_frame_is_the_clean_stage() -> None:

    base = glitch.Base(Image.new("RGB", (64, 32), (10, 200, 90)))
    assert bytes(glitch.glitch_frame(base, 0.0, 1)) == base.gbrp()


def test_glitch_frames_are_deterministic(scene) -> None:
    original, stages = scene
    a = [bytes(f) for f in glitch.frames(original, stages)]
    b = [bytes(f) for f in glitch.frames(original, stages)]
    assert a == b
