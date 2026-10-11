from typing import cast

import pytest
from PIL import Image

from nani_pix_bot.services.reveal import overlays, petals

from .ffprobe_helpers import decode_clean, probe_stream

HEIGHT = 60


def _frame(raw: bytes, size: tuple[int, int], index: int) -> Image.Image:
    step = size[0] * size[1] * 4
    return Image.frombytes("RGBA", size, raw[index * step : (index + 1) * step])


def _pink_xs(frame: Image.Image) -> list[int]:
    """The x of every clearly pink, clearly visible pixel."""
    data, width = frame.tobytes(), frame.width
    return [
        (i // 4) % width
        for i in range(0, len(data), 4)
        if data[i + 3] > 60 and data[i] > 200 and data[i] > data[i + 1] + 20
    ]


def _pink_pixels(frame: Image.Image) -> int:
    return len(_pink_xs(frame))


def _peak_alpha(channel: Image.Image) -> int:
    histogram = channel.histogram()
    return max(level for level, count in enumerate(histogram) if count)


@pytest.fixture(scope="module")
def drift() -> tuple[bytes, tuple[int, int]]:
    return petals.drift_frames(HEIGHT), petals.clip_size(HEIGHT)


def test_drift_has_every_frame_at_the_declared_size(drift) -> None:
    raw, size = drift
    assert size == (450, 216)
    assert len(raw) == petals.DRIFT_FRAMES * size[0] * size[1] * 4


def test_drift_shows_pink_petals_mid_ending(drift) -> None:
    raw, size = drift
    assert _pink_pixels(_frame(raw, size, petals.DRIFT_FRAMES // 2)) > 200


def test_drift_fades_in_and_out(drift) -> None:
    raw, size = drift
    first = _peak_alpha(_frame(raw, size, 0).getchannel("A"))
    middle = _peak_alpha(_frame(raw, size, petals.DRIFT_FRAMES // 2).getchannel("A"))
    last = _peak_alpha(_frame(raw, size, petals.DRIFT_FRAMES - 1).getchannel("A"))
    assert first < middle
    assert last < middle // 4


def test_drift_moves_the_petals(drift) -> None:
    raw, size = drift
    assert _frame(raw, size, 10).tobytes() != _frame(raw, size, 30).tobytes()


def test_drift_is_deterministic(drift) -> None:
    raw, _size = drift
    assert petals.drift_frames(HEIGHT) == raw


def test_drift_is_a_registered_win_overlay() -> None:
    assert overlays.WIN_OVERLAYS["petal_drift"] is petals.petal_drift_clip


@pytest.fixture(scope="module")
def storm() -> tuple[bytes, tuple[int, int]]:
    return petals.storm_frames(HEIGHT), petals.clip_size(HEIGHT)


def test_storm_has_every_frame_at_the_declared_size(storm) -> None:
    raw, size = storm
    assert len(raw) == petals.STORM_FRAMES * size[0] * size[1] * 4


def test_storm_is_busier_than_the_drift(drift, storm) -> None:
    middle = petals.DRIFT_FRAMES // 2
    (calm_raw, calm_size), (wild_raw, wild_size) = drift, storm
    calm = _pink_pixels(_frame(calm_raw, calm_size, middle))
    wild = _pink_pixels(_frame(wild_raw, wild_size, middle))
    assert wild > calm * 1.5


def test_storm_bursts_out_of_the_badge_then_the_gust_carries_it_right(storm) -> None:
    raw, size = storm

    def pink_centre_x(index: int) -> float:
        xs = _pink_xs(_frame(raw, size, index))
        return sum(xs) / len(xs)

    assert pink_centre_x(40) > pink_centre_x(8)


def test_storm_is_deterministic(storm) -> None:
    raw, _size = storm
    assert petals.storm_frames(HEIGHT) == raw


def test_storm_is_a_registered_win_overlay() -> None:
    assert overlays.WIN_OVERLAYS["sakura_storm"] is petals.sakura_storm_clip


def test_edge_fade_is_clear_inside_and_gone_at_the_edges() -> None:
    size = petals.clip_size(HEIGHT)
    w, h = size
    fade = petals.edge_fade(size, HEIGHT)
    assert fade.getpixel((w // 2, h // 2)) == 255
    for edge in ((0, h // 2), (w - 1, h // 2), (w // 2, 0), (w // 2, h - 1)):
        assert fade.getpixel(edge) == 0


def test_edge_fade_is_shorter_at_the_bottom() -> None:
    size = petals.clip_size(HEIGHT)
    w, h = size
    fade = petals.edge_fade(size, HEIGHT)
    depth = round(0.3 * HEIGHT)
    assert cast(int, fade.getpixel((w // 2, h - 1 - depth))) > cast(
        int, fade.getpixel((w // 2, depth))
    )


def test_no_petal_pixel_survives_on_the_outermost_rows(drift, storm) -> None:
    for raw, size in (drift, storm):
        w, h = size
        for index in range(0, petals.STORM_FRAMES, 7):
            alpha = _frame(raw, size, index).getchannel("A")
            edges = [alpha.crop(box) for box in ((0, 0, w, 1), (0, 0, 1, h), (w - 1, 0, w, h))]
            assert all(_peak_alpha(e) == 0 for e in edges)


@pytest.mark.ffmpeg
def test_storm_clip_is_a_decodable_qtrle_mov_of_the_declared_size() -> None:
    clip, size = petals.sakura_storm_clip(HEIGHT)
    assert decode_clean(clip)
    assert probe_stream(clip) == ("qtrle", *size, petals.STORM_FRAMES)


@pytest.mark.ffmpeg
def test_drift_clip_is_a_decodable_qtrle_mov_of_the_declared_size() -> None:
    clip, size = petals.petal_drift_clip(HEIGHT)
    assert decode_clean(clip)
    assert probe_stream(clip) == ("qtrle", *size, petals.DRIFT_FRAMES)
