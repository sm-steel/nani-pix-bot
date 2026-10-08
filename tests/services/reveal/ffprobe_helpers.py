"""ffprobe/ffmpeg helpers for reveal tests (not production code)."""

import subprocess
import tempfile
from pathlib import Path

from PIL import Image


def probe_frames(mp4: bytes) -> int:
    """Number of video packets in an MP4 (one per frame)."""
    with tempfile.TemporaryDirectory() as tmp:
        src = Path(tmp, "v.mp4")
        src.write_bytes(mp4)
        cmd = ["ffprobe", "-v", "error", "-count_packets", "-select_streams", "v:0",
               "-show_entries", "stream=nb_read_packets", "-of", "csv=p=0", str(src)]  # fmt: skip
        out = subprocess.run(cmd, capture_output=True, check=True)  # noqa: S603
        return int(out.stdout.decode().strip().strip(","))


def decode_clean(mp4: bytes) -> bool:
    """True if ffmpeg decodes the whole file without a single warning or error."""
    with tempfile.TemporaryDirectory() as tmp:
        src = Path(tmp, "v.mp4")
        src.write_bytes(mp4)
        cmd = ["ffmpeg", "-v", "error", "-nostdin", "-i", str(src), "-f", "null", "-"]
        proc = subprocess.run(cmd, capture_output=True, check=False)  # noqa: S603
        return proc.returncode == 0 and not proc.stderr.strip()


def decode_frames(mp4: bytes, size: tuple[int, int]) -> list[Image.Image]:
    """Every frame of an MP4 decoded to RGB images of `size` (tests only)."""
    with tempfile.TemporaryDirectory() as tmp:
        src = Path(tmp, "v.mp4")
        src.write_bytes(mp4)
        cmd = ["ffmpeg", "-v", "error", "-nostdin", "-i", str(src), "-f", "rawvideo",
               "-pix_fmt", "rgb24", "pipe:1"]  # fmt: skip
        raw = subprocess.run(cmd, capture_output=True, check=True).stdout  # noqa: S603
    n = size[0] * size[1] * 3
    return [Image.frombytes("RGB", size, raw[i : i + n]) for i in range(0, len(raw), n)]


def probe_stream(video: bytes) -> tuple[str, int, int, int]:
    """(codec, width, height, decoded frame count) of the first video stream of any container."""
    with tempfile.TemporaryDirectory() as tmp:
        src = Path(tmp, "v.bin")
        src.write_bytes(video)
        cmd = ["ffprobe", "-v", "error", "-count_frames", "-select_streams", "v:0",
               "-show_entries", "stream=codec_name,width,height,nb_read_frames",
               "-of", "csv=p=0", str(src)]  # fmt: skip
        out = subprocess.run(cmd, capture_output=True, check=True).stdout  # noqa: S603
    codec, width, height, frames = out.decode().strip().split(",")
    return codec, int(width), int(height), int(frames)
