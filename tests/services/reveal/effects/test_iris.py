import pytest
from PIL import Image

from nani_pix_bot.models.enums import RevealEffect
from nani_pix_bot.services.reveal import encode
from nani_pix_bot.services.reveal.effects import EFFECTS, _common, iris
from tests.services.reveal.effects.conftest import last_frame, mean_abs_diff
from tests.services.reveal.ffprobe_helpers import decode_clean, probe_frames

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
