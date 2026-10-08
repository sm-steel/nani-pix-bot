import gc
import subprocess
import sys
import tempfile
import warnings
from pathlib import Path

import pytest

from nani_pix_bot.services.reveal import encode

from .ffprobe_helpers import decode_clean, probe_frames

pytestmark = pytest.mark.ffmpeg
SIZE = (64, 48)


def _frames(n: int) -> list[bytes]:
    return [bytes([i * 4 % 256]) * (SIZE[0] * SIZE[1] * 3) for i in range(n)]


def _clear_yuv() -> bytes:
    return bytes([128]) * (SIZE[0] * SIZE[1] * 3 // 2)


def _overlay() -> encode.Overlay:
    """A tiny qtrle/argb confetti .mov (lavfi) plus a few small RGBA badge frames."""
    with tempfile.TemporaryDirectory() as tmp:
        mov = Path(tmp, "c.mov")
        subprocess.run(  # noqa: S603 - fixed argv
            ["ffmpeg", "-y", "-loglevel", "error", "-f", "lavfi", "-i",  # noqa: S607
             "color=c=red@0.5:s=16x12:r=30:d=0.5,format=argb",
             "-c:v", "qtrle", "-pix_fmt", "argb", str(mov)],
            check=True,
        )  # fmt: skip
        confetti = mov.read_bytes()
    badge_size = (20, 10)
    badge = b"".join(bytes([i * 60, 200, 50, 255]) * (20 * 10) for i in range(4))
    return encode.Overlay(confetti, badge, badge_size)


def test_x264_keeps_contract_and_swaps_crf() -> None:
    args = encode.x264(26)
    assert args[args.index("-crf") + 1] == "26"
    assert [a for a in args if a != "26"] == [a for a in encode.X264 if a != "21"]


def test_encode_part1_adds_the_opening_hold() -> None:
    mp4 = encode.encode_part1(_frames(10), SIZE)
    assert decode_clean(mp4)
    assert probe_frames(mp4) == 10 + round(encode.HOLD_START * encode.FPS)


def test_part1_and_ending_join_without_reencode() -> None:
    mp4 = encode.encode_part1(_frames(10), SIZE)
    ts, offset = encode.to_ts(mp4)
    final = encode.ending_and_join(ts, offset, _clear_yuv(), SIZE, overlay=None)
    assert decode_clean(final)
    assert probe_frames(final) == probe_frames(mp4) + encode.ENDING_FRAMES


def test_ending_with_overlay_joins_cleanly() -> None:
    mp4 = encode.encode_part1(_frames(10), SIZE)
    ts, offset = encode.to_ts(mp4)
    final = encode.ending_and_join(ts, offset, _clear_yuv(), SIZE, overlay=_overlay())
    assert decode_clean(final)
    assert probe_frames(final) == probe_frames(mp4) + encode.ENDING_FRAMES


def test_ffmpeg_failure_raises() -> None:
    with pytest.raises(encode.FfmpegError):
        encode.encode_part1([b"too-short"], SIZE)


def test_ending_failure_raises_ffmpeg_error_and_leaks_nothing() -> None:
    mp4 = encode.encode_part1(_frames(10), SIZE)
    ts, offset = encode.to_ts(mp4)
    junk = encode.Overlay(b"not a mov", _overlay().badge_rgba, (20, 10))
    with warnings.catch_warnings():
        warnings.simplefilter("error", ResourceWarning)
        with pytest.raises(encode.FfmpegError):
            encode.ending_and_join(ts, offset, _clear_yuv(), SIZE, overlay=junk)
        gc.collect()  # a leaked pipe would warn when collected


def test_missing_ffmpeg_raises_ffmpeg_error(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PATH", "")
    with pytest.raises(encode.FfmpegError):
        encode.encode_part1(_frames(2), SIZE)
    with pytest.raises(encode.FfmpegError):
        encode.to_ts(b"x")
    with pytest.raises(encode.FfmpegError):
        encode.ending_and_join(b"", 0.0, _clear_yuv(), SIZE, overlay=None)


def test_hung_ending_times_out(monkeypatch: pytest.MonkeyPatch) -> None:
    mp4 = encode.encode_part1(_frames(10), SIZE)
    ts, offset = encode.to_ts(mp4)
    monkeypatch.setattr(encode, "_ENDING_TIMEOUT", 0.001)
    with pytest.raises(encode.FfmpegError, match="timed out"):
        encode.ending_and_join(ts, offset, _clear_yuv(), SIZE, overlay=None)


def test_part1_timeout_kills_ffmpeg(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(encode, "_PART1_TIMEOUT", 0.001)
    with pytest.raises(encode.FfmpegError, match="timed out"):
        encode.encode_part1(_frames(10), SIZE)


def test_a_dead_encoder_stops_the_frame_producer_early() -> None:
    total, pulled = 500, []
    closed = []

    def _frames_gen():
        try:
            for i in range(total):
                pulled.append(i)
                yield bytes(1 << 20)  # bigger than any pipe buffer: the writer blocks or breaks
        finally:
            closed.append(True)

    dies = [sys.executable, "-c", "import sys; sys.exit(3)"]
    with pytest.raises(encode.FfmpegError, match="exited 3"):
        encode._pipe_frames(dies, _frames_gen())
    assert len(pulled) < total // 10
    assert closed  # the generator was closed, not left suspended
