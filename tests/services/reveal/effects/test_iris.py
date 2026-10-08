import pytest
from PIL import Image

from nani_pix_bot.models.enums import RevealEffect
from nani_pix_bot.services.reveal import encode
from nani_pix_bot.services.reveal.effects import EFFECTS, _common, iris
from tests.services.reveal.effects.conftest import last_frame, mean_abs_diff
from tests.services.reveal.ffprobe_helpers import decode_clean, decode_frames, probe_frames

pytestmark = pytest.mark.ffmpeg


def test_iris_part1_is_valid_and_ends_clear(scene) -> None:
    original, stages = scene
    mp4 = iris.render_part1(original, stages)
    assert decode_clean(mp4)
    assert probe_frames(mp4) > round(encode.HOLD_START * encode.FPS)
    assert mean_abs_diff(last_frame(mp4, original.size), original) < 6


def test_iris_is_registered() -> None:
    assert EFFECTS[RevealEffect.IRIS] is iris.render_part1


def test_easings_hit_their_endpoints() -> None:
    assert _common.ease_in_out(0) == 0
    assert _common.ease_in_out(1) == 1
    assert _common.ease_out_back(0) == pytest.approx(0)
    assert _common.ease_out_back(1) == pytest.approx(1)
    assert max(_common.ease_out_back(i / 20) for i in range(21)) > 1  # the overshoot pop


def test_yuv420_planes_have_the_right_sizes() -> None:
    y, u, v = _common.to_yuv420(Image.new("RGB", (8, 6), (255, 255, 255)))
    assert (len(y), len(u), len(v)) == (48, 12, 12)
    assert set(y) == {235}  # limited-range white
    assert set(u) == {128}


def test_focus_point_finds_the_skin_blob(scene) -> None:
    original, _ = scene
    fx, fy = iris.focus_point(original)
    assert 170 <= fx <= 240
    assert 30 <= fy <= 105


def test_iris_frame_arithmetic_is_exact(scene) -> None:
    original, stages = scene
    focus = iris.focus_point(original)
    hold = round(encode.HOLD_START * encode.FPS)
    open_n = int(encode.FPS * iris.OPEN_S)
    pause_n = int(encode.FPS * iris.PAUSE_S)
    burst_n = int(encode.FPS * iris.BURST_S)
    # constant-rate input: the front stage, the opening, the pause (the widest opening frame
    # repeated), the burst (its last step is the clear image itself), then the clear image once
    fed = 1 + open_n + pause_n + burst_n
    frames = list(iris._frames(iris.IrisFrames(original, stages[0], focus)))
    assert len(frames) == fed
    peak = frames[open_n]
    assert frames[open_n : open_n + pause_n + 1] == [peak] * (pause_n + 1)
    assert frames[open_n + pause_n + 1] != peak
    assert frames[-1] != frames[-2]  # the clear image is fed exactly once
    # the encoder only prepends the default opening hold
    mp4 = iris.render_part1(original, stages)
    assert probe_frames(mp4) == hold + fed
    assert len(decode_frames(mp4, original.size)) == probe_frames(mp4)
    assert mean_abs_diff(last_frame(mp4, original.size), original) < 6
