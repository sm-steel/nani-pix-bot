import pytest

from nani_pix_bot.models.enums import RevealEffect
from nani_pix_bot.services.reveal import encode
from nani_pix_bot.services.reveal.effects import EFFECTS, glitch
from tests.services.reveal.effects.conftest import last_frame, mean_abs_diff
from tests.services.reveal.ffprobe_helpers import decode_clean, probe_frames


@pytest.mark.ffmpeg
def test_glitch_part1_is_valid_and_ends_clear(scene) -> None:
    original, stages = scene
    mp4 = glitch.render_part1(original, stages)
    assert decode_clean(mp4)
    assert probe_frames(mp4) > round(encode.HOLD_START * encode.FPS)
    assert mean_abs_diff(last_frame(mp4, original.size), original) < 6


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
    from PIL import Image

    base = glitch.Base(Image.new("RGB", (64, 32), (10, 200, 90)))
    assert bytes(glitch.glitch_frame(base, 0.0, 1)) == base.gbrp()


def test_glitch_frames_are_deterministic(scene) -> None:
    original, stages = scene
    a = [bytes(f) for f in glitch.frames(original, stages)]
    b = [bytes(f) for f in glitch.frames(original, stages)]
    assert a == b
