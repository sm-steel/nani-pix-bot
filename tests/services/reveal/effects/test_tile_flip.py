import pytest
from PIL import Image

from nani_pix_bot.models.enums import RevealEffect
from nani_pix_bot.services.reveal import encode
from nani_pix_bot.services.reveal.effects import EFFECTS, tile_flip
from tests.services.reveal.effects.conftest import last_frame, mean_abs_diff
from tests.services.reveal.ffprobe_helpers import decode_clean, decode_frames, probe_frames


@pytest.mark.ffmpeg
def test_tile_flip_part1_is_valid_and_ends_clear(scene) -> None:
    original, stages = scene
    mp4 = tile_flip.render_part1(original, stages)
    assert decode_clean(mp4)
    assert probe_frames(mp4) > round(encode.HOLD_START * encode.FPS)
    assert mean_abs_diff(last_frame(mp4, original.size), original) < 6


def test_tile_flip_is_registered() -> None:
    assert EFFECTS[RevealEffect.TILE_FLIP] is tile_flip.render_part1


def test_frames_start_on_the_front_and_end_on_the_clear_image(scene) -> None:
    original, stages = scene
    frames = list(tile_flip.render_yuv(stages[0], original))
    first, last = frames[0], frames[-1]
    assert [len(p) for p in first] == [320 * 180, 160 * 90, 160 * 90]
    assert first != last
    clear = tile_flip.to_planes(original)
    assert last == tuple(p.tobytes() for p in clear)


def test_midflip_frames_show_darkened_gaps(scene) -> None:
    original, stages = scene
    frames = list(tile_flip.render_yuv(stages[0], original))
    mid = Image.frombytes("L", original.size, frames[len(frames) // 2][0])
    front = Image.frombytes("L", original.size, frames[0][0])
    assert min(mid.tobytes()) < min(front.tobytes())  # the shadow is darker than the front


def test_tile_flip_frame_arithmetic_is_exact(scene) -> None:
    original, stages = scene
    mp4 = tile_flip.render_part1(original, stages)
    hold = round(encode.HOLD_START * encode.FPS)  # tpad's cloned opening frames
    pieces = list(tile_flip.render_yuv(stages[0], original))
    clear = tuple(p.tobytes() for p in tile_flip.to_planes(original))
    # the last tile settles one step early, so the clear image is yielded twice - never three times
    assert pieces[-1] == pieces[-2] == clear
    assert pieces[-3] != clear
    assert probe_frames(mp4) == hold + len(pieces)
    img = decode_frames(mp4, original.size)
    assert len(img) == probe_frames(mp4)
    assert mean_abs_diff(img[-1], img[-3]) > 0  # held for those two frames, not more
    assert mean_abs_diff(img[-1], original) < 6
