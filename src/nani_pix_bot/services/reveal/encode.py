"""The only module that runs ffmpeg for the reveal video (issue #295).

Part 1 (opening hold + an effect's moving frames, ending on the first fully clear frame) is
encoded from streamed raw frames at game start and remuxed to MPEG-TS. At reveal time the
ending (clear image, optionally with confetti and the winner's badge) is encoded with the SAME
x264 settings and joined to part 1 with `-c copy`, which is only valid while both share the
exact SPS/PPS: every encode goes through `X264`/`x264()`. Only rate control (`-crf`) may differ.
"""

import contextlib
import functools
import queue
import subprocess
import tempfile
import threading
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path
from typing import IO, TypedDict, TypeVar, Unpack

from loguru import logger

FPS = 30
HOLD_START = 0.7
ENDING_FRAMES = 60  # 2 s of clear image (+ celebration) after part 1
_CRF = "21"
X264: tuple[str, ...] = (
    "-c:v", "libx264", "-preset", "veryfast", "-crf", _CRF, "-pix_fmt", "yuv420p",
    "-profile:v", "high", "-r", str(FPS), "-video_track_timescale", "15360", "-an",
)  # fmt: skip

_SHM = Path("/dev/shm")  # noqa: S108 - RAM-backed scratch on the Linux worker
_SCRATCH: Path | None = _SHM if _SHM.is_dir() else None
_STDERR_TAIL = 800
_QUEUE_DEPTH = 8
_FFMPEG = ("ffmpeg", "-y", "-nostdin", "-loglevel", "error")
_MUX = ("ffmpeg", "-y", "-loglevel", "error", "-f", "mpegts", "-i", "pipe:0", "-c", "copy",
        "-movflags", "+faststart")  # fmt: skip


class FfmpegError(RuntimeError):
    """An ffmpeg/ffprobe run failed; the message carries the tail of its stderr."""


@dataclass(frozen=True)
class Overlay:
    """The winner celebration laid over the ending: a qtrle/argb confetti .mov and the badge as
    raw RGBA frames of `badge_size` (the last one is held), both anchored bottom-left."""

    confetti_mov: bytes
    badge_rgba: bytes
    badge_size: tuple[int, int]
    margin: int = 20


def x264(crf: int | None = None) -> list[str]:
    """The shared encoder contract, optionally with another CRF (rate control isn't in the SPS)."""
    args = list(X264)
    if crf is not None:
        args[args.index("-crf") + 1] = str(crf)
    return args


def _tail(stderr: bytes) -> str:
    return stderr.decode(errors="replace").strip()[-_STDERR_TAIL:]


def _run(cmd: list[str], *, stdin_data: bytes | None = None) -> bytes:
    """Run one ffmpeg/ffprobe command; stdout on success, FfmpegError with stderr's tail if not."""
    proc = subprocess.run(cmd, input=stdin_data, capture_output=True, check=False)  # noqa: S603 - fixed argv
    if proc.returncode:
        raise FfmpegError(f"{cmd[0]} exited {proc.returncode}: {_tail(proc.stderr)}")
    return proc.stdout


_S = TypeVar("_S", bound=IO[bytes])


def _stream(stream: _S | None) -> _S:
    if stream is None:
        raise FfmpegError("ffmpeg pipe was not opened")
    return stream


def _tmp() -> tempfile.TemporaryDirectory[str]:
    return tempfile.TemporaryDirectory(dir=_SCRATCH)


def _drain_into(stdin: IO[bytes], frames: "queue.Queue[bytes | None]") -> None:
    broken = False
    while (frame := frames.get()) is not None:
        if broken:
            continue  # keep draining so the producer never blocks on a dead encoder
        try:
            stdin.write(frame)
        except OSError:
            broken = True
    with contextlib.suppress(OSError):  # the encoder already exited; the caller reports its code
        stdin.close()


def _pipe_frames(cmd: list[str], frames: Iterable[bytes]) -> None:
    """Stream `frames` into ffmpeg's stdin from a writer thread so the caller's generator
    overlaps with encoding; raise FfmpegError on a non-zero exit."""
    with tempfile.TemporaryFile() as err:
        proc = subprocess.Popen(cmd, stdin=subprocess.PIPE, stderr=err)  # noqa: S603 - fixed argv
        stdin = _stream(proc.stdin)
        pending: queue.Queue[bytes | None] = queue.Queue(maxsize=_QUEUE_DEPTH)
        writer = threading.Thread(target=_drain_into, args=(stdin, pending), daemon=True)
        writer.start()
        try:
            for frame in frames:
                pending.put(frame)
        finally:
            pending.put(None)
            writer.join()
            code = proc.wait()
        if code:
            err.seek(0)
            raise FfmpegError(f"ffmpeg exited {code}: {_tail(err.read())}")


class Part1Options(TypedDict, total=False):
    """Keyword options of `encode_part1`."""

    pix_fmt: str  # raw input pixel format (default rgb24)
    input_fps: int  # frame rate of the raw input (default FPS)
    vf: str  # replaces the default opening-hold `tpad` filter
    max_frames: int  # cap on the output frame count
    crf: int  # replaces the contract's default CRF


def encode_part1(
    frames: Iterable[bytes], size: tuple[int, int], **opts: Unpack[Part1Options]
) -> bytes:
    """Raw frames (streamed) -> part 1 MP4. `vf` defaults to the opening-hold `tpad`."""
    w, h = size
    max_frames = opts.get("max_frames")
    with _tmp() as tmp:
        out = Path(tmp, "part1.mp4")
        cmd = [
            *_FFMPEG,
            "-f", "rawvideo", "-pix_fmt", opts.get("pix_fmt", "rgb24"), "-s", f"{w}x{h}",
            "-r", str(opts.get("input_fps", FPS)), "-i", "pipe:0",
            "-vf", opts.get("vf") or f"tpad=start_mode=clone:start_duration={HOLD_START}",
            *(["-frames:v", str(max_frames)] if max_frames else []),
            *x264(opts.get("crf")), str(out),
        ]  # fmt: skip
        _pipe_frames(cmd, frames)
        return out.read_bytes()


def _pts(path: Path) -> list[float]:
    out = _run(["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries",
                "packet=pts_time", "-of", "csv=p=0", str(path)])  # fmt: skip
    return [float(x.strip(",")) for x in out.decode().split() if x.strip(",")]


@functools.lru_cache(maxsize=1)
def _part2_base() -> float:
    """Where an MPEG-TS ending encoded with X264 + `-output_ts_offset K` starts, minus K. Constant
    for given encoder settings (the TS muxer's delay), so it's measured, once per process."""
    k = 10.0
    with _tmp() as tmp:
        probe = Path(tmp, "probe.ts")
        _run([*_FFMPEG, "-f", "lavfi", "-i", f"color=s=64x64:r={FPS}:d=0.2", *X264,
              "-output_ts_offset", str(k), "-f", "mpegts", str(probe)])  # fmt: skip
        return min(_pts(probe)) - k


def to_ts(part1_mp4: bytes) -> tuple[bytes, float]:
    """Remux part 1 to MPEG-TS (no re-encode). Also returns the `-output_ts_offset` the ending
    needs so its first frame lands 1/FPS after part 1's last."""
    with _tmp() as tmp:
        src, dst = Path(tmp, "p1.mp4"), Path(tmp, "p1.ts")
        src.write_bytes(part1_mp4)
        _run([*_FFMPEG, "-i", str(src), "-c", "copy", "-f", "mpegts", str(dst)])
        next_pts = max(_pts(dst)) + 1 / FPS
        return dst.read_bytes(), next_pts - _part2_base()


def _ending_cmd(
    clear_yuv: Path, size: tuple[int, int], offset: float, overlay: Overlay | None, confetti: Path
) -> list[str]:
    w, h = size
    cmd = [*_FFMPEG, "-f", "rawvideo", "-pix_fmt", "yuv420p", "-s", f"{w}x{h}",
           "-framerate", str(FPS), "-i", str(clear_yuv)]  # fmt: skip
    loop = f"[0:v]loop=loop={ENDING_FRAMES - 1}:size=1:start=0,setpts=N/{FPS}/TB"
    if overlay is None:
        graph = f"{loop}[o]"
    else:
        bw, bh = overlay.badge_size
        at = f"x={overlay.margin}:y=main_h-overlay_h-{overlay.margin}"
        graph = (f"{loop}[bg];[bg][1:v]overlay={at}:eof_action=pass:format=yuv420[c];"
                 f"[c][2:v]overlay={at}:eof_action=repeat:format=yuv420[o]")  # fmt: skip
        cmd += ["-i", str(confetti), "-f", "rawvideo", "-pix_fmt", "rgba", "-s", f"{bw}x{bh}",
                "-framerate", str(FPS), "-i", "pipe:0"]  # fmt: skip
    return [*cmd, "-filter_complex", graph, "-map", "[o]", "-frames:v", str(ENDING_FRAMES), *X264,
            "-output_ts_offset", f"{offset:.6f}", "-f", "mpegts", "pipe:1"]  # fmt: skip


def ending_and_join(
    part1_ts: bytes,
    join_offset: float,
    clear_yuv: bytes,
    size: tuple[int, int],
    overlay: Overlay | None,
) -> bytes:
    """Encode the ending and join it to part 1 -> final MP4 (`-c copy`, faststart).

    The encoder and the muxer run concurrently (one startup hidden behind the other): part 1's
    TS is written into the muxer first, then the encoder's TS is relayed after it."""
    with _tmp() as tmp, tempfile.TemporaryFile() as enc_err, tempfile.TemporaryFile() as mux_err:
        clear, confetti, out = (
            Path(tmp, "clear.yuv"),
            Path(tmp, "confetti.mov"),
            Path(tmp, "final.mp4"),
        )
        clear.write_bytes(clear_yuv)
        if overlay is not None:
            confetti.write_bytes(overlay.confetti_mov)
        cmd = _ending_cmd(clear, size, join_offset, overlay, confetti)
        enc = subprocess.Popen(  # noqa: S603 - fixed argv, no shell
            cmd,
            stdin=subprocess.PIPE if overlay else subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=enc_err,
        )
        mux = subprocess.Popen(  # noqa: S603 - fixed argv, no shell
            [*_MUX, str(out)], stdin=subprocess.PIPE, stderr=mux_err
        )
        relay = (part1_ts, _stream(enc.stdout), _stream(mux.stdin))
        thread = threading.Thread(target=_relay, args=relay, daemon=True)
        thread.start()
        if overlay is not None:
            stdin = _stream(enc.stdin)
            with contextlib.suppress(OSError):  # encoder died early; its code is reported below
                stdin.write(overlay.badge_rgba)
                stdin.close()
        thread.join()
        for name, proc, err in (("encoder", enc, enc_err), ("muxer", mux, mux_err)):
            if code := proc.wait():
                err.seek(0)
                raise FfmpegError(f"ending {name} exited {code}: {_tail(err.read())}")
        data = out.read_bytes()
    logger.debug(
        "ending joined: {size} bytes, overlay={overlay}",
        size=len(data),
        overlay=overlay is not None,
    )
    return data


def _relay(part1_ts: bytes, encoder_out: IO[bytes], muxer_in: IO[bytes]) -> None:
    """Part 1's TS into the muxer first, then everything the ending encoder emits."""
    try:
        muxer_in.write(part1_ts)
        while chunk := encoder_out.read(1 << 16):
            muxer_in.write(chunk)
    except OSError:
        encoder_out.read()  # a dead muxer: let the encoder finish; its exit code reports it
    finally:
        with contextlib.suppress(OSError):
            muxer_in.close()


def probe_frames(mp4: bytes) -> int:
    """Number of video packets in an MP4 (one per frame)."""
    with _tmp() as tmp:
        src = Path(tmp, "v.mp4")
        src.write_bytes(mp4)
        out = _run(["ffprobe", "-v", "error", "-count_packets", "-select_streams", "v:0",
                    "-show_entries", "stream=nb_read_packets", "-of", "csv=p=0",
                    str(src)])  # fmt: skip
        return int(out.decode().strip().strip(","))


def decode_clean(mp4: bytes) -> bool:
    """True if ffmpeg decodes the whole file without a single warning or error."""
    with _tmp() as tmp:
        src = Path(tmp, "v.mp4")
        src.write_bytes(mp4)
        cmd = ["ffmpeg", "-v", "error", "-nostdin", "-i", str(src), "-f", "null", "-"]
        proc = subprocess.run(cmd, capture_output=True, check=False)  # noqa: S603
        return proc.returncode == 0 and not proc.stderr.strip()
