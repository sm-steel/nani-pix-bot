import pytest
from PIL import Image, ImageStat

from nani_pix_bot.models.enums import RevealEffect
from nani_pix_bot.services import sprites
from nani_pix_bot.services.reveal import encode
from nani_pix_bot.services.reveal.effects import EFFECTS, candle_snuff
from tests.services.reveal.effects.conftest import last_frame, mean_abs_diff
from tests.services.reveal.ffprobe_helpers import decode_clean, probe_frames


def _frames(original: Image.Image, stages: list[Image.Image]) -> list[Image.Image]:
    return [
        Image.frombytes("RGB", original.size, raw) for raw in candle_snuff.frames(original, stages)
    ]


def test_candle_snuff_is_registered() -> None:
    assert EFFECTS[RevealEffect.CANDLE_SNUFF] is candle_snuff.render_part1


def test_frames_end_exactly_on_the_clear_image(scene) -> None:
    original, stages = scene
    assert _frames(original, stages)[-1].tobytes() == original.tobytes()


def test_the_first_frame_is_the_front_by_candlelight(scene) -> None:
    original, stages = scene
    first = _frames(original, stages)[0]
    w, h = original.size
    fx = round(w * candle_snuff.CANDLE_X)
    fy = round(h * (1 - candle_snuff.CANDLE_BOTTOM - candle_snuff.CANDLE_H))
    near = (fx - w // 12, fy - h // 12, fx - w // 30, fy + h // 12)  # beside the candle
    assert mean_abs_diff(first.crop(near), stages[0].crop(near)) < 25
    corner = (0, 0, w // 8, h // 8)
    lit = sum(ImageStat.Stat(stages[0].crop(corner)).mean)
    dark = sum(ImageStat.Stat(first.crop(corner)).mean)
    assert lit - dark > 60


def test_the_darkness_lifts_from_the_bottom() -> None:
    size = (320, 180)
    clear, front = Image.new("RGB", size, (220, 30, 30)), Image.new("RGB", size, (30, 30, 220))
    raw = list(candle_snuff.frames(clear, [front]))
    seconds = candle_snuff.FLICKER_S + candle_snuff.SNUFF_S + candle_snuff.LIFT_S / 2
    middle = Image.frombytes("RGB", size, raw[round(seconds * encode.FPS)])
    w, h = size
    bottom = ImageStat.Stat(middle.crop((w // 2, h - h // 8, w, h))).mean
    top = ImageStat.Stat(middle.crop((0, 0, w, h // 8))).mean
    assert bottom[0] > 180  # fully clear (red), not a half-blended band
    assert bottom[2] < 80
    assert sum(top) < 200  # still dark


def test_candle_snuff_is_deterministic(scene) -> None:
    original, stages = scene
    assert list(candle_snuff.frames(original, stages)) == list(
        candle_snuff.frames(original, stages)
    )


@pytest.mark.parametrize("size", [(180, 320), (64, 36)])
def test_candle_snuff_handles_portrait_and_tiny_images(size) -> None:
    clear, front = Image.new("RGB", size, (200, 120, 40)), Image.new("RGB", size, (90, 90, 90))
    assert _frames(clear, [front])[-1].tobytes() == clear.tobytes()


def test_a_missing_candle_sprite_still_renders_and_logs_once(monkeypatch, caplog) -> None:
    def gone(name: str, height: int) -> Image.Image:
        raise FileNotFoundError(name)

    monkeypatch.setattr(sprites, "sprite_height", gone)
    monkeypatch.setattr(candle_snuff, "_missing_logged", False)
    size = (160, 90)
    clear, front = Image.new("RGB", size, (200, 120, 40)), Image.new("RGB", size, (90, 90, 90))
    assert _frames(clear, [front])[-1].tobytes() == clear.tobytes()
    _frames(clear, [front])
    errors = [r for r in caplog.records if r.levelname == "ERROR"]
    assert len(errors) == 1
    assert "candle.png" in errors[0].getMessage()


@pytest.mark.ffmpeg
def test_candle_snuff_part1_is_valid_and_ends_clear(scene) -> None:
    original, stages = scene
    mp4 = candle_snuff.render_part1(original, stages)
    assert decode_clean(mp4)
    assert probe_frames(mp4) > round(encode.HOLD_START * encode.FPS)
    assert mean_abs_diff(last_frame(mp4, original.size), original) < 6
