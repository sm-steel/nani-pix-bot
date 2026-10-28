from typing import cast

import pytest
from PIL import Image, ImageChops, ImageStat

from nani_pix_bot.models.enums import RevealEffect
from nani_pix_bot.services import sprites
from nani_pix_bot.services.reveal import encode
from nani_pix_bot.services.reveal.effects import EFFECTS, night_parade
from tests.services.reveal.effects.conftest import last_frame, mean_abs_diff
from tests.services.reveal.ffprobe_helpers import decode_clean, probe_frames


def _frames(original: Image.Image, stages: list[Image.Image]) -> list[Image.Image]:
    return [
        Image.frombytes("RGB", original.size, raw) for raw in night_parade.frames(original, stages)
    ]


def _at(frames: list[Image.Image], seconds: float) -> Image.Image:
    return frames[round(seconds * encode.FPS)]


def test_night_parade_is_registered() -> None:
    assert EFFECTS[RevealEffect.NIGHT_PARADE] is night_parade.render_part1


def test_frames_end_exactly_on_the_clear_image(scene) -> None:
    original, stages = scene
    assert _frames(original, stages)[-1].tobytes() == original.tobytes()


def test_the_clear_image_follows_the_parade_from_the_left() -> None:
    size = (320, 180)
    clear, front = Image.new("RGB", size, (220, 30, 30)), Image.new("RGB", size, (30, 30, 220))
    middle = _at(_frames(clear, [front]), night_parade.MARCH_S / 2)
    w, h = size
    left = ImageStat.Stat(middle.crop((0, 0, w // 8, h // 2))).mean  # above the walkers
    right = ImageStat.Stat(middle.crop((w - w // 8, 0, w, h // 2))).mean
    assert left[0] > 180
    assert left[2] < 80
    assert right[2] > 100  # still the front,
    assert right[0] < 80


def _silhouette_pixels(frame: Image.Image) -> tuple[int, float]:
    """(how many pixels are within 12 of SILHOUETTE, their centroid x)."""
    dark = night_parade.SILHOUETTE
    far = [
        ImageChops.difference(frame.getchannel(i), Image.new("L", frame.size, dark[i]))
        for i in range(3)
    ]
    mask = ImageChops.lighter(ImageChops.lighter(far[0], far[1]), far[2])
    mask = mask.point(lambda v: 255 if v <= 12 else 0)
    columns = cast(
        list[int], list(mask.resize((frame.width, 1), Image.Resampling.BOX).get_flattened_data())
    )
    total = sum(columns)
    if not total:
        return 0, 0.0
    return round(total * frame.height / 255), sum(x * v for x, v in enumerate(columns)) / total


def test_silhouettes_march_across(scene) -> None:
    original, stages = scene
    frames = _frames(original, stages)
    first = _silhouette_pixels(_at(frames, 0.25))
    later = _silhouette_pixels(_at(frames, 0.25 + 0.3))
    assert first[0] > 500
    assert later[0] > 500
    assert later[1] > first[1]


def test_night_parade_is_deterministic(scene) -> None:
    original, stages = scene
    assert list(night_parade.frames(original, stages)) == list(
        night_parade.frames(original, stages)
    )


@pytest.mark.parametrize("size", [(180, 320), (64, 36)])
def test_night_parade_handles_portrait_and_tiny_images(size) -> None:
    clear, front = Image.new("RGB", size, (200, 120, 40)), Image.new("RGB", size, (90, 90, 90))
    raw = [frame.tobytes() for frame in _frames(clear, [front])]
    assert len(raw) > round(encode.HOLD_START * encode.FPS)
    assert {len(frame) for frame in raw} == {size[0] * size[1] * 3}
    assert raw[0] == front.tobytes()
    assert raw[-1] == clear.tobytes()
    assert raw[len(raw) // 2] not in (raw[0], raw[-1])  # mid-transition is neither end


def test_a_missing_sprite_still_renders_and_logs_once(monkeypatch, caplog) -> None:
    def gone(name: str, height: int) -> Image.Image:
        raise FileNotFoundError(name)

    monkeypatch.setattr(sprites, "sprite_height", gone)
    size = (160, 90)
    clear, front = Image.new("RGB", size, (200, 120, 40)), Image.new("RGB", size, (90, 90, 90))
    assert _frames(clear, [front])[-1].tobytes() == clear.tobytes()
    _frames(clear, [front])
    errors = [r for r in caplog.records if r.levelname == "ERROR"]
    assert len(errors) == len(night_parade.WALKERS)  # once per missing sprite
    assert "kitsune.png" in " ".join(r.getMessage() for r in errors)


@pytest.mark.ffmpeg
def test_night_parade_part1_is_valid_and_ends_clear(scene) -> None:
    original, stages = scene
    mp4 = night_parade.render_part1(original, stages)
    assert decode_clean(mp4)
    assert probe_frames(mp4) > round(encode.HOLD_START * encode.FPS)
    assert mean_abs_diff(last_frame(mp4, original.size), original) < 6


def test_the_front_ahead_of_the_leader_is_dimmed_after_the_moonlight_fades_in() -> None:
    size = (320, 180)
    blue = Image.new("RGB", size, (30, 30, 220))
    frames = _frames(Image.new("RGB", size, (220, 30, 30)), [blue])
    w, h = size

    def blue_ahead(frame: Image.Image) -> float:
        return ImageStat.Stat(frame.crop((w - w // 8, 0, w, h // 4))).mean[2]

    assert blue_ahead(frames[0]) == pytest.approx(220, abs=1)  # the held front, not yet dimmed
    seconds = night_parade.MOON_IN_S + 1 / encode.FPS
    assert blue_ahead(_at(frames, seconds)) == pytest.approx(176, abs=3)  # 0.8 of 220


@pytest.mark.parametrize(("size", "tight"), [((1280, 720), False), ((360, 640), True)])
def test_walkers_never_overlap_and_landscape_spacing_is_unchanged(size, tight) -> None:
    image = Image.new("RGB", size)
    look = night_parade._look(image, [image])
    widest = max(walker.art.width for walker in look.walkers)
    assert look.spacing >= widest  # side by side, never merged into one blob
    assert (look.spacing > size[0] * night_parade.SPACING) is tight
