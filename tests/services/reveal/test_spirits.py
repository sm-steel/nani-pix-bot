import pytest
from PIL import Image, ImageDraw

from nani_pix_bot.services.reveal import overlay_canvas, overlays, spirits

from .ffprobe_helpers import decode_clean, probe_stream

HEIGHT = 60


def _frame(raw: bytes, size: tuple[int, int], index: int) -> Image.Image:
    step = size[0] * size[1] * 4
    return Image.frombytes("RGBA", size, raw[index * step : (index + 1) * step])


def _wing_ys(frame: Image.Image) -> list[int]:
    """The y of every clearly visible dark purple pixel."""
    data, width = frame.tobytes(), frame.width
    return [
        (i // 4) // width
        for i in range(0, len(data), 4)
        if data[i + 3] > 60 and data[i + 2] > data[i] > data[i + 1] and max(data[i : i + 3]) < 90
    ]


def _rim_pixels(frame: Image.Image) -> int:
    data = frame.tobytes()
    return sum(
        1
        for i in range(0, len(data), 4)
        if data[i + 3] > 60 and data[i] > 200 and 90 < data[i + 1] < 190 and data[i + 2] < 90
    )


def _peak_alpha(channel: Image.Image) -> int:
    histogram = channel.histogram()
    return max(level for level, count in enumerate(histogram) if count)


def _one_bat(flap: float) -> Image.Image:
    image = Image.new("RGBA", (200, 200), (0, 0, 0, 0))
    spirits.draw_bat(ImageDraw.Draw(image), (50, 50), 40, flap, 255)
    return image


@pytest.fixture(scope="module")
def bats() -> tuple[bytes, tuple[int, int]]:
    return spirits.bat_frames(HEIGHT), overlay_canvas.clip_size(HEIGHT)


def test_bats_have_every_frame_at_the_declared_size(bats) -> None:
    raw, size = bats
    assert size == overlay_canvas.clip_size(60)
    assert len(raw) == spirits.BAT_FRAMES * size[0] * size[1] * 4


def test_bats_show_dark_purple_wings_mid_ending(bats) -> None:
    raw, size = bats
    assert len(_wing_ys(_frame(raw, size, spirits.BAT_FRAMES // 2))) > 150


def test_bats_have_an_orange_rim_along_the_wing_tops(bats) -> None:
    raw, size = bats
    assert _rim_pixels(_frame(raw, size, spirits.BAT_FRAMES // 2)) > 20


def test_bats_rise_over_the_ending(bats) -> None:
    raw, size = bats

    def centroid_y(index: int) -> float:
        ys = _wing_ys(_frame(raw, size, index))
        return sum(ys) / len(ys)

    assert centroid_y(40) < centroid_y(10) - 0.3 * HEIGHT


def test_a_flapping_bat_raises_and_drops_its_wing_tips() -> None:
    up, down = _one_bat(1.0), _one_bat(0.0)
    up_box, down_box = up.getbbox(), down.getbbox()
    assert up_box is not None
    assert down_box is not None
    assert up_box[1] < down_box[1]  # tips raised: the silhouette reaches higher
    assert up_box[2] > down_box[2]  # and spreads wider


def test_the_flap_cycles_between_wings_up_and_wings_down() -> None:
    flaps = [spirits._flap(0.4, f) for f in range(12)]
    assert max(flaps) > 0.9
    assert min(flaps) < 0.2


def test_bats_flap_between_neighbouring_frames(bats) -> None:
    raw, size = bats
    assert _frame(raw, size, 20).tobytes() != _frame(raw, size, 21).tobytes()


def test_the_rendered_bats_use_the_flap(bats, monkeypatch) -> None:
    raw, _size = bats
    monkeypatch.setattr(spirits, "_flap", lambda _phase, _f: 0.5)
    assert spirits.bat_frames(HEIGHT) != raw


def test_bats_fade_in_and_out(bats) -> None:
    raw, size = bats
    first = _peak_alpha(_frame(raw, size, 0).getchannel("A"))
    middle = _peak_alpha(_frame(raw, size, spirits.BAT_FRAMES // 2).getchannel("A"))
    last = _peak_alpha(_frame(raw, size, spirits.BAT_FRAMES - 1).getchannel("A"))
    assert first < middle
    assert last < middle // 4


def test_bats_are_deterministic(bats) -> None:
    raw, _size = bats
    assert spirits.bat_frames(HEIGHT) == raw


def test_bat_burst_is_a_registered_win_overlay() -> None:
    assert overlays.WIN_OVERLAYS["bat_burst"] is spirits.bat_burst_clip


@pytest.mark.ffmpeg
def test_bat_burst_clip_decodes() -> None:
    clip, size = spirits.bat_burst_clip(HEIGHT)
    assert decode_clean(clip)
    assert probe_stream(clip) == ("qtrle", *size, spirits.BAT_FRAMES)
