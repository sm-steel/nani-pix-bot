import subprocess
import tempfile
from pathlib import Path

import pytest
from PIL import Image

from nani_pix_bot.models.enums import RevealEffect
from nani_pix_bot.services.reveal import encode
from nani_pix_bot.services.reveal.effects import EFFECTS, shatter
from tests.services.reveal.effects.conftest import last_frame, mean_abs_diff
from tests.services.reveal.ffprobe_helpers import decode_clean, probe_frames


@pytest.mark.ffmpeg
def test_shatter_part1_is_valid_and_ends_clear(scene) -> None:
    original, stages = scene
    mp4 = shatter.render_part1(original, stages)
    assert decode_clean(mp4)
    assert probe_frames(mp4) > round(encode.HOLD_START * encode.FPS)
    assert mean_abs_diff(last_frame(mp4, original.size), original) < 6


def _decode_all(mp4: bytes, size: tuple[int, int]) -> list[bytes]:
    with tempfile.TemporaryDirectory() as tmp:
        src = Path(tmp, "v.mp4")
        src.write_bytes(mp4)
        cmd = ["ffmpeg", "-v", "error", "-i", str(src), "-f", "rawvideo", "-pix_fmt", "rgb24",
               "pipe:1"]  # fmt: skip
        raw = subprocess.run(cmd, capture_output=True, check=True).stdout  # noqa: S603
    n = size[0] * size[1] * 3
    return [raw[i : i + n] for i in range(0, len(raw), n)]


@pytest.mark.ffmpeg
def test_shatter_frame_arithmetic_is_exact(scene) -> None:
    original, stages = scene
    mp4 = shatter.render_part1(original, stages)
    hold = round(encode.HOLD_START * encode.FPS)  # tpad's cloned opening frames
    pieces = list(shatter.frames(stages[0], original))  # shake + shatter, clear frame last
    n_in = len(pieces)
    assert pieces[-1] != pieces[-2]  # the clear frame appears exactly once
    assert probe_frames(mp4) == hold + n_in
    decoded = _decode_all(mp4, original.size)
    assert len(decoded) == probe_frames(mp4)
    img = [Image.frombytes("RGB", original.size, d) for d in decoded]
    assert mean_abs_diff(img[-1], original) < 6  # and the video ends on it
    assert all(mean_abs_diff(img[0], f) < 0.5 for f in img[:hold])  # static mosaic hold


def test_shatter_is_registered() -> None:
    assert EFFECTS[RevealEffect.SHATTER] is shatter.render_part1


def test_frames_are_yuv420p_planes_and_end_on_the_clear_image(scene) -> None:
    original, stages = scene
    out = list(shatter.frames(stages[0], original))
    n = 320 * 180
    assert all(tuple(map(len, f)) == (n, n // 4, n // 4) for f in out)
    assert out[-1] == tuple(p.tobytes() for p in shatter._planes(original))
    assert out[0] != out[-1]
    assert out[1] != out[0]  # the shake moves the mosaic
    assert out[-2] != out[-1]  # exactly one clear frame, not repeated


def test_shatter_frames_are_deterministic(scene) -> None:
    original, stages = scene
    assert list(shatter.frames(stages[0], original)) == list(shatter.frames(stages[0], original))


def test_shatter_scales_a_small_stage_to_the_original(scene) -> None:
    original, stages = scene
    small = stages[0].resize((64, 36), Image.Resampling.NEAREST)
    mp4 = shatter.render_part1(original, [small])
    assert decode_clean(mp4)
