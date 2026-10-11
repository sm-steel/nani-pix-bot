import pytest
from PIL import Image

from nani_pix_bot.services.reveal import overlays, petals

from .ffprobe_helpers import decode_clean, probe_stream

HEIGHT = 60


def _frame(raw: bytes, size: tuple[int, int], index: int) -> Image.Image:
    step = size[0] * size[1] * 4
    return Image.frombytes("RGBA", size, raw[index * step : (index + 1) * step])


def _pink_pixels(frame: Image.Image) -> int:
    data = frame.tobytes()
    pixels = (data[i : i + 4] for i in range(0, len(data), 4))
    return sum(1 for r, g, b, a in pixels if a > 60 and r > 200 and r > g + 20 and b > g)


def _peak_alpha(frame: Image.Image) -> int:
    histogram = frame.getchannel("A").histogram()
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
    first = _peak_alpha(_frame(raw, size, 0))
    middle = _peak_alpha(_frame(raw, size, petals.DRIFT_FRAMES // 2))
    last = _peak_alpha(_frame(raw, size, petals.DRIFT_FRAMES - 1))
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


@pytest.mark.ffmpeg
def test_drift_clip_is_a_decodable_qtrle_mov_of_the_declared_size() -> None:
    clip, size = petals.petal_drift_clip(HEIGHT)
    assert decode_clean(clip)
    assert probe_stream(clip) == ("qtrle", *size, petals.DRIFT_FRAMES)
