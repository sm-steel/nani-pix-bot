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
from typing import IO, TypeVar

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
# Hard limits: a hung or missing ffmpeg must never block the reveal.
_PART1_TIMEOUT = 120.0
_REMUX_TIMEOUT = 30.0  # MP4 -> TS remux, ffprobe and the one-off offset measurement
_ENDING_TIMEOUT = 60.0
_CLIP_TIMEOUT = 60.0
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


def _run(cmd: list[str], *, timeout: float, stdin_data: bytes | None = None) -> bytes:
    """Run one ffmpeg/ffprobe command; stdout on success, FfmpegError with stderr's tail if not
    (non-zero exit, timeout - the process is killed - or a missing binary)."""
    try:
        proc = subprocess.run(  # noqa: S603 - fixed argv, no shell
            cmd, input=stdin_data, capture_output=True, check=False, timeout=timeout
        )
    except subprocess.TimeoutExpired as exc:
        raise FfmpegError(f"{cmd[0]} timed out after {timeout:g}s") from exc
    except OSError as exc:
        raise FfmpegError(f"{cmd[0]} could not run: {exc}") from exc
    if proc.returncode:
        raise FfmpegError(f"{cmd[0]} exited {proc.returncode}: {_tail(proc.stderr)}")
    return proc.stdout


@dataclass(frozen=True)
class _Stdio:
    stdin: int | IO[bytes] | None = None
    stdout: int | IO[bytes] | None = None
    stderr: int | IO[bytes] | None = None


def _popen(
    cmd: list[str], procs: list[subprocess.Popen[bytes]], stdio: _Stdio
) -> subprocess.Popen[bytes]:
    """Popen that registers the process for cleanup and maps a missing binary to FfmpegError."""
    try:
        proc = subprocess.Popen(  # noqa: S603 - fixed argv, no shell
            cmd, stdin=stdio.stdin, stdout=stdio.stdout, stderr=stdio.stderr
        )
    except OSError as exc:
        raise FfmpegError(f"{cmd[0]} could not start: {exc}") from exc
    procs.append(proc)
    return proc


class _Watchdog:
    """Kills the registered processes if `timeout` passes before `cancel()`."""

    def __init__(self, timeout: float, procs: list[subprocess.Popen[bytes]]) -> None:
        self.fired = False
        self._procs = procs
        self._timer = threading.Timer(timeout, self._fire)
        self._timer.daemon = True
        self._timer.start()

    def _fire(self) -> None:
        self.fired = True
        _kill(self._procs)

    def cancel(self) -> None:
        self._timer.cancel()


def _kill(procs: list[subprocess.Popen[bytes]]) -> None:
    for proc in list(procs):
        with contextlib.suppress(OSError):
            proc.kill()


def _reap(procs: list[subprocess.Popen[bytes]]) -> None:
    """Cleanup for any exit path: kill whatever still runs (on success nothing does), wait for
    all, close every pipe."""
    for proc in procs:
        if proc.poll() is None:
            with contextlib.suppress(OSError):
                proc.kill()
        proc.wait()
        for pipe in (proc.stdin, proc.stdout):
            if pipe is not None:
                with contextlib.suppress(OSError):
                    pipe.close()


_S = TypeVar("_S", bound=IO[bytes])


def _stream(stream: _S | None) -> _S:
    if stream is None:
        raise FfmpegError("ffmpeg pipe was not opened")
    return stream


def _tmp() -> tempfile.TemporaryDirectory[str]:
    return tempfile.TemporaryDirectory(dir=_SCRATCH)


def _drain_into(
    stdin: IO[bytes], frames: "queue.Queue[bytes | None]", dead: threading.Event
) -> None:
    while (frame := frames.get()) is not None:
        if dead.is_set():
            continue  # keep draining so the producer never blocks on a dead encoder
        try:
            stdin.write(frame)
        except OSError:
            dead.set()
    with contextlib.suppress(OSError):  # the encoder already exited; the caller reports its code
        stdin.close()


def _feed(proc: subprocess.Popen[bytes], frames: Iterable[bytes]) -> None:
    """Hand `frames` to the writer thread. Once the encoder is dead (broken pipe or exited) the
    producer is stopped early (the iterator is closed) instead of rendering the remaining
    frames."""
    pending: queue.Queue[bytes | None] = queue.Queue(maxsize=_QUEUE_DEPTH)
    dead = threading.Event()
    writer = threading.Thread(
        target=_drain_into, args=(_stream(proc.stdin), pending, dead), daemon=True
    )
    writer.start()
    try:
        for frame in frames:
            pending.put(frame)
            if dead.is_set() or proc.poll() is not None:
                break
    finally:
        try:
            close = getattr(frames, "close", None)
            if close is not None:
                with contextlib.suppress(Exception):  # a generator's own error must not mask
                    close()  # the encoder's FfmpegError the caller reports
        finally:
            pending.put(None)
            writer.join()


def _pipe_frames(cmd: list[str], frames: Iterable[bytes]) -> None:
    """Stream `frames` into ffmpeg's stdin from a writer thread so the caller's generator
    overlaps with encoding; raise FfmpegError on a non-zero exit, timeout or missing ffmpeg."""
    procs: list[subprocess.Popen[bytes]] = []
    dog = _Watchdog(_PART1_TIMEOUT, procs)
    with tempfile.TemporaryFile() as err:
        try:
            proc = _popen(cmd, procs, _Stdio(stdin=subprocess.PIPE, stderr=err))
            _feed(proc, frames)
            proc.wait()
        finally:
            dog.cancel()
            _reap(procs)
        if dog.fired:
            raise FfmpegError(f"ffmpeg timed out after {_PART1_TIMEOUT:g}s")
        if code := procs[0].returncode:
            err.seek(0)
            raise FfmpegError(f"ffmpeg exited {code}: {_tail(err.read())}")


@dataclass(frozen=True)
class Part1Options:
    """Options of `encode_part1`."""

    pix_fmt: str = "rgb24"  # raw input pixel format
    input_fps: int = FPS  # frame rate of the raw input
    vf: str | None = None  # replaces the default opening-hold `tpad` filter
    max_frames: int | None = None  # cap on the output frame count
    crf: int | None = None  # replaces the contract's default CRF


def encode_part1(
    frames: Iterable[bytes], size: tuple[int, int], options: Part1Options | None = None
) -> bytes:
    """Raw frames (streamed) -> part 1 MP4. `vf` defaults to the opening-hold `tpad`."""
    w, h = size
    opts = options or Part1Options()
    with _tmp() as tmp:
        out = Path(tmp, "part1.mp4")
        cmd = [
            *_FFMPEG,
            "-f", "rawvideo", "-pix_fmt", opts.pix_fmt, "-s", f"{w}x{h}",
            "-r", str(opts.input_fps), "-i", "pipe:0",
            "-vf", opts.vf or f"tpad=start_mode=clone:start_duration={HOLD_START}",
            *(["-frames:v", str(opts.max_frames)] if opts.max_frames else []),
            *x264(opts.crf), str(out),
        ]  # fmt: skip
        _pipe_frames(cmd, frames)
        return out.read_bytes()


def encode_rgba_clip(frames: bytes, size: tuple[int, int]) -> bytes:
    """Concatenated raw RGBA frames of `size` -> a qtrle/argb .mov (mostly-transparent frames
    run-length encode to almost nothing and decode for free), the confetti `Overlay` format."""
    w, h = size
    with _tmp() as tmp:
        out = Path(tmp, "clip.mov")
        cmd = [*_FFMPEG, "-f", "rawvideo", "-pix_fmt", "rgba", "-s", f"{w}x{h}", "-r", str(FPS),
               "-i", "pipe:0", "-c:v", "qtrle", "-pix_fmt", "argb", str(out)]  # fmt: skip
        _run(cmd, timeout=_CLIP_TIMEOUT, stdin_data=frames)
        return out.read_bytes()


def _pts(path: Path) -> list[float]:
    cmd = ["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries", "packet=pts_time",
           "-of", "csv=p=0", str(path)]  # fmt: skip
    out = _run(cmd, timeout=_REMUX_TIMEOUT)
    return [float(x.strip(",")) for x in out.decode().split() if x.strip(",")]


@functools.lru_cache(maxsize=1)
def _part2_base() -> float:
    """Where an MPEG-TS ending encoded with X264 + `-output_ts_offset K` starts, minus K. Constant
    for given encoder settings (the TS muxer's delay), so it's measured, once per process."""
    k = 10.0
    with _tmp() as tmp:
        probe = Path(tmp, "probe.ts")
        cmd = [*_FFMPEG, "-f", "lavfi", "-i", f"color=s=64x64:r={FPS}:d=0.2", *X264,
               "-output_ts_offset", str(k), "-f", "mpegts", str(probe)]  # fmt: skip
        _run(cmd, timeout=_REMUX_TIMEOUT)
        return min(_pts(probe)) - k


def to_ts(part1_mp4: bytes) -> tuple[bytes, float]:
    """Remux part 1 to MPEG-TS (no re-encode). Also returns the `-output_ts_offset` the ending
    needs so its first frame lands 1/FPS after part 1's last."""
    with _tmp() as tmp:
        src, dst = Path(tmp, "p1.mp4"), Path(tmp, "p1.ts")
        src.write_bytes(part1_mp4)
        cmd = [*_FFMPEG, "-i", str(src), "-c", "copy", "-f", "mpegts", str(dst)]
        _run(cmd, timeout=_REMUX_TIMEOUT)
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
        procs: list[subprocess.Popen[bytes]] = []
        dog = _Watchdog(_ENDING_TIMEOUT, procs)
        try:
            cmds = (
                _ending_cmd(clear, size, join_offset, overlay, confetti),
                [*_MUX, str(out)],
            )
            _run_ending(cmds, part1_ts, overlay.badge_rgba if overlay else None, procs,
                        (enc_err, mux_err))  # fmt: skip
        finally:
            dog.cancel()
            _reap(procs)
        if dog.fired:
            raise FfmpegError(f"ending timed out after {_ENDING_TIMEOUT:g}s")
        for name, proc, err in zip(("encoder", "muxer"), procs, (enc_err, mux_err), strict=True):
            if code := proc.returncode:
                err.seek(0)
                raise FfmpegError(f"ending {name} exited {code}: {_tail(err.read())}")
        return out.read_bytes()


def _run_ending(
    cmds: tuple[list[str], list[str]],
    part1_ts: bytes,
    badge: bytes | None,
    procs: list[subprocess.Popen[bytes]],
    errs: tuple[IO[bytes], IO[bytes]],
) -> None:
    """Start the ending encoder and the muxer (`cmds`) together and run them to completion.
    Everything started is registered in `procs`, so the caller can reap it on any exit path."""
    badge_in = subprocess.DEVNULL if badge is None else subprocess.PIPE
    enc = _popen(cmds[0], procs, _Stdio(badge_in, subprocess.PIPE, errs[0]))
    mux = _popen(cmds[1], procs, _Stdio(subprocess.PIPE, None, errs[1]))
    relay = (part1_ts, _stream(enc.stdout), _stream(mux.stdin))
    thread = threading.Thread(target=_relay, args=relay, daemon=True)
    thread.start()
    try:
        if badge is not None:
            stdin = _stream(enc.stdin)
            with contextlib.suppress(OSError):  # encoder died early; its code is reported later
                stdin.write(badge)
            with contextlib.suppress(OSError):
                stdin.close()
    finally:
        thread.join(_ENDING_TIMEOUT)
    for proc in procs:
        proc.wait()


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
