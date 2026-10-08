"""ffprobe/ffmpeg helpers for reveal tests (not production code)."""

import subprocess
import tempfile
from pathlib import Path


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
