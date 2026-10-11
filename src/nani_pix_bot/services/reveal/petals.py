"""Sakura win overlays for the reveal ending (seasons spec §7): sakura petals
over the winner's badge, as a qtrle/argb .mov like celebration.confetti_clip.
Runs in the reveal worker: module-level functions, seeded, no logging."""

import math
import random
from collections.abc import Callable
from dataclasses import dataclass

from PIL import Image, ImageChops, ImageDraw

from nani_pix_bot.services.reveal import encode
from nani_pix_bot.services.shapes import petal_outline

DRIFT_FRAMES = 56  # the ending is 60 frames; the petals fade out just before it ends
DRIFT_PETALS = 18
DRIFT_SEED = 11
FADE_IN = 6
FADE_OUT = 14
SS = 2  # supersampling of each frame
EDGE_FADE = 0.7  # petals dissolve over this many badge heights before the top and sides
EDGE_FADE_BOTTOM = 0.3
PINKS: tuple[tuple[int, int, int], ...] = (
    (255, 183, 197),
    (250, 160, 185),
    (255, 214, 224),
    (244, 140, 170),
)
VEIN = (220, 110, 140)

STORM_FRAMES = 56
STORM_PETALS = 50
STORM_BLOSSOMS = 5
STORM_SPARKLES = 22
STORM_SEED = 23
STORM_BURST_TAU = 5.0  # frames for the burst to slow to a third
STORM_GUST_START = 10  # frame the gust begins to build
BLOSSOM_PINK = (255, 196, 210)
BLOSSOM_CORE = (232, 84, 120)
SPARKLE = (255, 246, 214)


@dataclass(frozen=True)
class _Petal:
    x: float
    y: float
    vx: float  # wind
    vy: float  # fall
    sway: float  # side-to-side amplitude
    sway_speed: float
    phase: float
    angle: float
    spin: float
    flutter_speed: float
    length: float
    color: tuple[int, int, int]


def _drift_petals(height: int, size: tuple[int, int]) -> list[_Petal]:
    rng = random.Random(DRIFT_SEED)  # noqa: S311 - decoration, seeded for a reproducible look
    w, h = size
    return [
        _Petal(
            x=rng.uniform(-0.5 * height, w * 0.85),
            y=rng.uniform(-0.6 * height, h * 0.55),
            vx=rng.uniform(0.02, 0.05) * height,
            vy=rng.uniform(0.035, 0.07) * height,
            sway=rng.uniform(0.08, 0.2) * height,
            sway_speed=rng.uniform(0.08, 0.16),
            phase=rng.uniform(0, math.tau),
            angle=rng.uniform(0, math.tau),
            spin=rng.uniform(-0.08, 0.08),
            flutter_speed=rng.uniform(0.12, 0.25),
            length=rng.uniform(0.3, 0.48) * height,
            color=rng.choice(PINKS),
        )
        for _ in range(DRIFT_PETALS)
    ]


def _alpha(f: int, frames: int) -> int:
    fade = min(1.0, (f + 1) / FADE_IN, (frames - f) / FADE_OUT)
    return round(255 * max(0.0, fade))


@dataclass(frozen=True)
class _Pose:
    """Where a petal is in one frame, in clip pixels."""

    x: float
    y: float
    angle: float
    flutter: float  # 0..1: how much of its width faces the camera


def _flutter(phase: float, speed: float, f: int) -> float:
    """A petal turning over in the air: its width narrows and widens."""
    return 0.25 + 0.75 * abs(math.cos(phase + speed * f))


def _draw_petal(
    draw: ImageDraw.ImageDraw, pose: _Pose, length: float, color: tuple[int, int, int], alpha: int
) -> None:
    x, y, size = pose.x * SS, pose.y * SS, length * SS
    outline = petal_outline((x, y), size, size * 0.42 * pose.flutter, pose.angle)
    draw.polygon(outline, fill=(*color, alpha))
    tip = (x + math.cos(pose.angle) * size * 0.6, y + math.sin(pose.angle) * size * 0.6)
    draw.line(((x, y), tip), fill=(*VEIN, alpha // 2), width=SS)


def _drift_pose(p: _Petal, f: int) -> _Pose:
    return _Pose(
        x=p.x + p.vx * f + p.sway * math.sin(p.phase + p.sway_speed * f),
        y=p.y + p.vy * f,
        angle=p.angle + p.spin * f,
        flutter=_flutter(p.phase, p.flutter_speed, f),
    )


def _smooth(t: float) -> float:
    t = max(0.0, min(1.0, t))
    return t * t * (3 - 2 * t)


def edge_fade(size: tuple[int, int], height: int) -> Image.Image:
    """An L mask over the overlay area: 255 inside, easing to 0 at its edges, so
    a petal leaving the area dissolves instead of being cut off on a straight line.
    The bottom fades over a shorter distance: the badge sits there."""
    w, h = size
    side, bottom = EDGE_FADE * height, EDGE_FADE_BOTTOM * height
    across = Image.new("L", (w, 1))
    for x in range(w):
        across.putpixel((x, 0), round(255 * _smooth(min(x, w - 1 - x) / side)))
    down = Image.new("L", (1, h))
    for y in range(h):
        down.putpixel((0, y), round(255 * min(_smooth(y / side), _smooth((h - 1 - y) / bottom))))
    return ImageChops.multiply(across.resize(size), down.resize(size))


def _render(
    size: tuple[int, int],
    height: int,
    frames: int,
    draw_frame: Callable[[ImageDraw.ImageDraw, int, int], None],
) -> bytes:
    """Raw RGBA frames, one after another; `draw_frame(draw, f, alpha)` paints
    frame `f` onto a SS-times larger canvas, which then fades out at its edges."""
    w, h = size
    fade = edge_fade(size, height)
    raw = bytearray()
    for f in range(frames):
        big = Image.new("RGBA", (w * SS, h * SS), (0, 0, 0, 0))
        draw_frame(ImageDraw.Draw(big), f, _alpha(f, frames))
        frame = big.resize(size, Image.Resampling.BOX)
        frame.putalpha(ImageChops.multiply(frame.getchannel("A"), fade))
        raw += frame.tobytes()
    return bytes(raw)


def clip_size(height: int) -> tuple[int, int]:
    """The overlay area for a badge `height`, the same as the confetti's."""
    return round(height * 7.5), height + round(height * 2.6)


def drift_frames(height: int) -> bytes:
    size = clip_size(height)
    petals = _drift_petals(height, size)

    def draw_frame(draw: ImageDraw.ImageDraw, f: int, alpha: int) -> None:
        for p in petals:
            _draw_petal(draw, _drift_pose(p, f), p.length, p.color, alpha)

    return _render(size, height, DRIFT_FRAMES, draw_frame)


@dataclass(frozen=True)
class _Storm:
    """One petal (or whole blossom) of the storm: burst out of the badge, then
    swept right by the gust in a rolling swirl."""

    x: float  # where it bursts from
    y: float
    vx: float  # burst velocity
    vy: float
    wind: float  # how hard the gust pushes this one
    swirl: float
    swirl_speed: float
    phase: float
    angle: float
    spin: float
    flutter_speed: float
    length: float
    color: tuple[int, int, int]


@dataclass(frozen=True)
class _Sparkle:
    x: float
    y: float
    phase: float
    size: float


def _storm_petals(height: int, count: int, length: tuple[float, float], seed: int) -> list[_Storm]:
    rng = random.Random(seed)  # noqa: S311 - decoration, seeded for a reproducible look
    _w, h = clip_size(height)
    out = []
    for _ in range(count):
        heading = math.radians(rng.uniform(-170, -10))  # up and out of the badge
        speed = rng.uniform(0.25, 0.6) * height
        out.append(
            _Storm(
                x=rng.uniform(0.6 * height, 4.2 * height),
                y=h - 0.5 * height,
                vx=math.cos(heading) * speed * 1.3,
                vy=math.sin(heading) * speed,
                wind=rng.uniform(0.7, 1.3),
                swirl=rng.uniform(0.15, 0.4) * height,
                swirl_speed=rng.uniform(0.18, 0.3),
                phase=rng.uniform(0, math.tau),
                angle=rng.uniform(0, math.tau),
                spin=rng.uniform(-0.25, 0.25),
                flutter_speed=rng.uniform(0.2, 0.4),
                length=rng.uniform(*length) * height,
                color=rng.choice(PINKS),
            )
        )
    return out


def _gust(f: int, height: int) -> float:
    """How far the gust has carried a petal by frame `f`: calm, then building."""
    return max(0.0, f - STORM_GUST_START) ** 1.5 * height * 0.012


def _storm_pose(p: _Storm, f: int, height: int) -> _Pose:
    burst = 1 - math.exp(-f / STORM_BURST_TAU)  # the puff slows as it spreads
    rise = -0.0006 * height * f * f  # the swirl lifts as the gust takes over
    return _Pose(
        x=p.x + p.vx * STORM_BURST_TAU * burst + p.wind * _gust(f, height),
        y=p.y
        + p.vy * STORM_BURST_TAU * burst
        + rise
        + p.swirl * math.sin(p.phase + p.swirl_speed * f),
        angle=p.angle + p.spin * f * (1 + f / 20),
        flutter=_flutter(p.phase, p.flutter_speed, f),
    )


def _draw_blossom(draw: ImageDraw.ImageDraw, pose: _Pose, length: float, alpha: int) -> None:
    """A whole five-petal blossom tumbling through the storm."""
    x, y, size = pose.x * SS, pose.y * SS, length * SS
    squash = 0.45 + 0.55 * pose.flutter
    for k in range(5):
        angle = pose.angle + k * math.tau / 5
        petal = petal_outline((x, y), size, size * 0.42 * squash, angle)
        draw.polygon(petal, fill=(*BLOSSOM_PINK, alpha))
    core = size * 0.16
    draw.ellipse((x - core, y - core, x + core, y + core), fill=(*BLOSSOM_CORE, alpha))


def _sparkles(height: int) -> list[_Sparkle]:
    rng = random.Random(STORM_SEED + 1)  # noqa: S311 - decoration, seeded for a reproducible look
    w, h = clip_size(height)
    return [
        _Sparkle(
            x=rng.uniform(0, w),
            y=rng.uniform(0, h - height),
            phase=rng.uniform(0, math.tau),
            size=rng.uniform(0.06, 0.12) * height,
        )
        for _ in range(STORM_SPARKLES)
    ]


def _draw_sparkle(draw: ImageDraw.ImageDraw, s: _Sparkle, f: int, alpha: int) -> None:
    """A four-point twinkle that flares and fades."""
    twinkle = max(0.0, math.sin(s.phase + f * 0.45)) ** 2
    if twinkle < 0.05:
        return
    x, y, r = s.x * SS, s.y * SS, s.size * SS * twinkle
    thin = r * 0.22
    a = round(alpha * twinkle)
    draw.polygon([(x - r, y), (x, y - thin), (x + r, y), (x, y + thin)], fill=(*SPARKLE, a))
    draw.polygon([(x, y - r), (x + thin, y), (x, y + r), (x - thin, y)], fill=(*SPARKLE, a))


def storm_frames(height: int) -> bytes:
    size = clip_size(height)
    petals = _storm_petals(height, STORM_PETALS, (0.28, 0.45), STORM_SEED)
    blossoms = _storm_petals(height, STORM_BLOSSOMS, (0.28, 0.36), STORM_SEED + 2)
    sparkles = _sparkles(height)

    def draw_frame(draw: ImageDraw.ImageDraw, f: int, alpha: int) -> None:
        for p in petals:
            _draw_petal(draw, _storm_pose(p, f, height), p.length, p.color, alpha)
        for b in blossoms:
            _draw_blossom(draw, _storm_pose(b, f, height), b.length, alpha)
        for s in sparkles:
            _draw_sparkle(draw, s, f, alpha)

    return _render(size, height, STORM_FRAMES, draw_frame)


def sakura_storm_clip(height: int) -> tuple[bytes, tuple[int, int]]:
    """A storm of sakura: petals and whole blossoms burst out of the badge, then
    a building gust sweeps them right in a rolling swirl, sparkles twinkling."""
    size = clip_size(height)
    return encode.encode_rgba_clip(storm_frames(height), size), size


def petal_drift_clip(height: int) -> tuple[bytes, tuple[int, int]]:
    """Sakura petals drifting down over the badge on a light wind, swaying and
    turning over, for the whole ending (`height` is the badge height)."""
    size = clip_size(height)
    return encode.encode_rgba_clip(drift_frames(height), size), size
