import math

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
        assert ys, f"no wing pixels in frame {index}"
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
    flaps = [spirits._bat_flap(0.4, f) for f in range(12)]
    assert max(flaps) > 0.9
    assert min(flaps) < 0.2


def test_bats_flap_between_neighbouring_frames(bats) -> None:
    raw, size = bats
    assert _frame(raw, size, 20).tobytes() != _frame(raw, size, 21).tobytes()


def test_the_rendered_bats_use_the_flap(bats, monkeypatch) -> None:
    raw, _size = bats
    monkeypatch.setattr(spirits, "_bat_flap", lambda _phase, _f: 0.5)
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


def _flame_pixels(frame: Image.Image) -> set[tuple[int, int]]:
    """Every clearly visible blue-white pixel."""
    data, width = frame.tobytes(), frame.width
    return {
        ((i // 4) % width, (i // 4) // width)
        for i in range(0, len(data), 4)
        if data[i + 3] > 60 and data[i + 2] > 200 and data[i + 2] > data[i] + 20
    }


@pytest.fixture(scope="module")
def flames() -> bytes:
    return spirits.hitodama_frames(HEIGHT)


def test_hitodama_size_and_frame_count(flames) -> None:
    size = overlay_canvas.clip_size(HEIGHT)
    assert len(flames) == spirits.HITODAMA_FRAMES * size[0] * size[1] * 4


def test_hitodama_shows_blue_white_flames_mid_ending(flames) -> None:
    size = overlay_canvas.clip_size(HEIGHT)
    mid = _frame(flames, size, spirits.HITODAMA_FRAMES // 2)
    assert len(_flame_pixels(mid)) > 150


def test_the_flames_orbit(flames) -> None:
    size = overlay_canvas.clip_size(HEIGHT)
    early = _flame_pixels(_frame(flames, size, 10))
    late = _flame_pixels(_frame(flames, size, 30))
    assert early
    assert late
    assert len(early & late) < 0.5 * len(early | late)  # mostly somewhere else


def test_the_flames_sit_above_the_badge(flames) -> None:
    size = overlay_canvas.clip_size(HEIGHT)
    ys = [y for _x, y in _flame_pixels(_frame(flames, size, spirits.HITODAMA_FRAMES // 2))]
    assert max(ys) < size[1] - HEIGHT  # none reaches down into the badge


def test_hitodama_fades_in_and_out(flames) -> None:
    size = overlay_canvas.clip_size(HEIGHT)
    n = spirits.HITODAMA_FRAMES
    first = _peak_alpha(_frame(flames, size, 0).getchannel("A"))
    middle = _peak_alpha(_frame(flames, size, n // 2).getchannel("A"))
    last = _peak_alpha(_frame(flames, size, n - 1).getchannel("A"))
    assert first < middle
    assert last < middle // 4


def test_hitodama_is_deterministic(flames) -> None:
    assert spirits.hitodama_frames(HEIGHT) == flames


def test_hitodama_is_a_registered_win_overlay() -> None:
    assert overlays.WIN_OVERLAYS["hitodama"] is spirits.hitodama_clip


@pytest.mark.ffmpeg
def test_hitodama_clip_decodes() -> None:
    clip, size = spirits.hitodama_clip(HEIGHT)
    assert decode_clean(clip)
    assert probe_stream(clip) == ("qtrle", *size, spirits.HITODAMA_FRAMES)


def test_every_flame_travels_along_its_orbit() -> None:
    for fl in spirits._flames(spirits.FLAMES, spirits.HITODAMA_SEED):
        x0, y0, _ = spirits._orbit(fl, 10, HEIGHT)
        x1, y1, _ = spirits._orbit(fl, 30, HEIGHT)
        assert math.hypot(x1 - x0, y1 - y0) > 0.3 * HEIGHT


def test_the_flames_flicker_through_the_art_while_held_in_place(monkeypatch) -> None:
    monkeypatch.setattr(spirits, "_orbit", lambda _fl, _f, _h: (150.0, 100.0, 1.0))
    raw = spirits.hitodama_frames(HEIGHT)
    size = overlay_canvas.clip_size(HEIGHT)
    f = spirits.FLICKER_HOLD * 4  # first frame of an art frame, fully faded in
    same_art = _frame(raw, size, f).tobytes() == _frame(raw, size, f + 1).tobytes()
    next_art = (
        _frame(raw, size, f).tobytes() == _frame(raw, size, f + spirits.FLICKER_HOLD).tobytes()
    )
    assert same_art
    assert not next_art


def test_the_far_flames_are_fainter_than_the_near_ones(monkeypatch) -> None:
    size = overlay_canvas.clip_size(HEIGHT)
    peaks = []
    monkeypatch.setattr(spirits, "FLAMES", 1)
    for near in (0.0, 1.0):
        monkeypatch.setattr(spirits, "_orbit", lambda _fl, _f, _h, n=near: (225.0, 100.0, n))
        raw = spirits.hitodama_frames(HEIGHT)
        peaks.append(_peak_alpha(_frame(raw, size, spirits.HITODAMA_FRAMES // 2).getchannel("A")))
    assert peaks[0] < peaks[1]


def test_missing_art_leaves_an_empty_clip_instead_of_failing(tmp_path, monkeypatch) -> None:
    from nani_pix_bot.services import sprites

    monkeypatch.setattr(sprites, "SPRITE_DIR", tmp_path)
    sprites.sprite.cache_clear()
    sprites.sprite_height.cache_clear()
    try:
        raw = spirits.hitodama_frames(HEIGHT)
    finally:
        sprites.sprite.cache_clear()
        sprites.sprite_height.cache_clear()
    size = overlay_canvas.clip_size(HEIGHT)
    assert len(raw) == spirits.HITODAMA_FRAMES * size[0] * size[1] * 4
    assert not any(raw)
