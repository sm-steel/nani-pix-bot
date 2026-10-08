import subprocess
import tempfile
from pathlib import Path

import pytest

from nani_pix_bot.services.reveal import encode

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
    assert encode.decode_clean(mp4)
    assert encode.probe_frames(mp4) == 10 + round(encode.HOLD_START * encode.FPS)


def test_part1_and_ending_join_without_reencode() -> None:
    mp4 = encode.encode_part1(_frames(10), SIZE)
    ts, offset = encode.to_ts(mp4)
    final = encode.ending_and_join(ts, offset, _clear_yuv(), SIZE, overlay=None)
    assert encode.decode_clean(final)
    assert encode.probe_frames(final) == encode.probe_frames(mp4) + encode.ENDING_FRAMES


def test_ending_with_overlay_joins_cleanly() -> None:
    mp4 = encode.encode_part1(_frames(10), SIZE)
    ts, offset = encode.to_ts(mp4)
    final = encode.ending_and_join(ts, offset, _clear_yuv(), SIZE, overlay=_overlay())
    assert encode.decode_clean(final)
    assert encode.probe_frames(final) == encode.probe_frames(mp4) + encode.ENDING_FRAMES


def test_ffmpeg_failure_raises() -> None:
    with pytest.raises(encode.FfmpegError):
        encode.encode_part1([b"too-short"], SIZE)
