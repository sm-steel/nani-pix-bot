import pytest
from PIL import Image, ImageChops, ImageStat

from nani_pix_bot.models.enums import RevealEffect
from nani_pix_bot.services import sprites
from nani_pix_bot.services.reveal import encode
from nani_pix_bot.services.reveal.effects import EFFECTS, _common, gashadokuro_rise
from tests.services.reveal.effects.conftest import last_frame, mean_abs_diff
from tests.services.reveal.ffprobe_helpers import decode_clean, probe_frames

SIZE = (320, 180)
RED, BLUE = (220, 30, 30), (30, 30, 220)


def _frames(original: Image.Image, stages: list[Image.Image]) -> list[Image.Image]:
    return [
        Image.frombytes("RGB", original.size, raw)
        for raw in gashadokuro_rise.frames(original, stages)
    ]


def _red_blue(size: tuple[int, int] = SIZE) -> list[Image.Image]:
    return _frames(Image.new("RGB", size, RED), [Image.new("RGB", size, BLUE)])


def _at(frames: list[Image.Image], seconds: float) -> Image.Image:
    return frames[round(seconds * encode.FPS)]


def _mean(frame: Image.Image, box: tuple[int, int, int, int]) -> list[float]:
    return ImageStat.Stat(frame.crop(box)).mean


def test_gashadokuro_rise_is_registered() -> None:
    assert EFFECTS[RevealEffect.GASHADOKURO_RISE] is gashadokuro_rise.render_part1


def test_frames_end_exactly_on_the_clear_image(scene) -> None:
    original, stages = scene
    assert _frames(original, stages)[-1].tobytes() == original.tobytes()


def test_the_hand_sweeps_left_to_right() -> None:
    middle = _at(_red_blue(), gashadokuro_rise.RISE_S + gashadokuro_rise.SWEEP_S / 2)
    w, h = SIZE
    left = _mean(middle, (0, 0, w // 8, h // 4))  # above the hand's reach
    right = _mean(middle, (w - w // 8, 0, w, h // 4))
    assert left[0] > 150
    assert left[2] < 100
    assert right[2] > 150
    assert right[0] < 100


def _bone_pixels(frame: Image.Image) -> int:
    r, g, b = frame.split()
    bright = ImageChops.darker(ImageChops.darker(r, g), b).point(lambda v: 255 if v > 200 else 0)
    greyish = ImageChops.difference(r, b).point(lambda v: 255 if v < 40 else 0)
    return ImageChops.multiply(bright, greyish).histogram()[255]


def test_the_hand_shows_ivory_bone() -> None:
    # the bone is ivory (only its highlights pass the grey-ish test): a larger scene, so the
    # hand is big enough for 800 of them
    assert max(_bone_pixels(frame) for frame in _red_blue((640, 360))) > 800


def test_the_hand_is_out_of_sight_before_it_rises_and_after_it_sinks() -> None:
    frames = _red_blue()
    assert _bone_pixels(frames[0]) == 0
    assert _bone_pixels(frames[-2]) == 0  # the last frame before the clear one


def _blue_pixels(frame: Image.Image) -> int:
    r, _, b = frame.split()
    bluish = b.point(lambda v: 255 if v > 120 else 0)
    not_red = r.point(lambda v: 255 if v < 120 else 0)
    return ImageChops.multiply(bluish, not_red).histogram()[255]


def test_cleared_blocks_fly_off() -> None:
    """Front-coloured (blue) squares sit inside the cleared (red) left part mid-sweep."""
    middle = _at(_red_blue(), gashadokuro_rise.RISE_S + gashadokuro_rise.SWEEP_S / 2)
    w, h = SIZE
    cleared = middle.crop(
        (w // 4, 0, w // 2 - 20, h // 2)
    )  # just behind the hand, above its fingers
    assert _blue_pixels(cleared) > 100


def test_gashadokuro_rise_is_deterministic(scene) -> None:
    original, stages = scene
    assert list(gashadokuro_rise.frames(original, stages)) == list(
        gashadokuro_rise.frames(original, stages)
    )


@pytest.mark.parametrize("size", [(180, 320), (64, 36)])
def test_gashadokuro_rise_handles_portrait_and_tiny_images(size) -> None:
    clear, front = Image.new("RGB", size, (200, 120, 40)), Image.new("RGB", size, (90, 90, 90))
    assert _frames(clear, [front])[-1].tobytes() == clear.tobytes()


def test_a_missing_hand_still_clears_the_blocks_and_logs_once(monkeypatch, caplog) -> None:
    def gone(name: str, height: int) -> Image.Image:
        raise FileNotFoundError(name)

    monkeypatch.setattr(sprites, "sprite_height", gone)
    monkeypatch.setattr(_common, "_logged_missing", set())
    frames = _red_blue()
    _red_blue()
    assert frames[-1].tobytes() == Image.new("RGB", SIZE, RED).tobytes()
    assert _bone_pixels(max(frames, key=_bone_pixels)) == 0
    left = _mean(
        _at(frames, gashadokuro_rise.RISE_S + gashadokuro_rise.SWEEP_S / 2), (0, 0, 40, 90)
    )
    assert left[0] > 150  # the left is cleared even with no hand
    errors = [r for r in caplog.records if r.levelname == "ERROR"]
    assert len(errors) == 1
    assert "skeleton_hand.png" in errors[0].getMessage()


@pytest.mark.ffmpeg
def test_gashadokuro_rise_part1_is_valid_and_ends_clear(scene) -> None:
    original, stages = scene
    mp4 = gashadokuro_rise.render_part1(original, stages)
    assert decode_clean(mp4)
    assert probe_frames(mp4) > round(encode.HOLD_START * encode.FPS)
    assert mean_abs_diff(last_frame(mp4, original.size), original) < 6
