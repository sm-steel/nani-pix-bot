"""Sakura win overlays for the reveal ending (seasons spec §7): sakura petals
over the winner's badge, as a qtrle/argb .mov like celebration.confetti_clip.
Runs in the reveal worker: module-level functions, seeded, no logging."""

import math
import random
from dataclasses import dataclass

from PIL import Image, ImageDraw

from nani_pix_bot.services.reveal import encode
from nani_pix_bot.services.shapes import petal_outline

DRIFT_FRAMES = 56  # the ending is 60 frames; the petals fade out just before it ends
DRIFT_PETALS = 18
DRIFT_SEED = 11
FADE_IN = 6
FADE_OUT = 14
SS = 2  # supersampling of each frame
PINKS: tuple[tuple[int, int, int], ...] = (
    (255, 183, 197),
    (250, 160, 185),
    (255, 214, 224),
    (244, 140, 170),
)
VEIN = (220, 110, 140)


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


def _draw_petal(draw: ImageDraw.ImageDraw, p: _Petal, f: int, alpha: int) -> None:
    x = (p.x + p.vx * f + p.sway * math.sin(p.phase + p.sway_speed * f)) * SS
    y = (p.y + p.vy * f) * SS
    angle = p.angle + p.spin * f
    # A petal turning over in the air: its width narrows and widens.
    flutter = 0.25 + 0.75 * abs(math.cos(p.phase + p.flutter_speed * f))
    length = p.length * SS
    outline = petal_outline((x, y), length, length * 0.42 * flutter, angle)
    draw.polygon(outline, fill=(*p.color, alpha))
    tip = (x + math.cos(angle) * length * 0.6, y + math.sin(angle) * length * 0.6)
    draw.line(((x, y), tip), fill=(*VEIN, alpha // 2), width=SS)


def _frames(petals: list[_Petal], size: tuple[int, int], frames: int) -> bytes:
    """Raw RGBA frames, one after another."""
    w, h = size
    raw = bytearray()
    for f in range(frames):
        big = Image.new("RGBA", (w * SS, h * SS), (0, 0, 0, 0))
        draw = ImageDraw.Draw(big)
        alpha = _alpha(f, frames)
        for p in petals:
            _draw_petal(draw, p, f, alpha)
        raw += big.resize(size, Image.Resampling.BOX).tobytes()
    return bytes(raw)


def clip_size(height: int) -> tuple[int, int]:
    """The overlay area for a badge `height`, the same as the confetti's."""
    return round(height * 7.5), height + round(height * 2.6)


def drift_frames(height: int) -> bytes:
    size = clip_size(height)
    return _frames(_drift_petals(height, size), size, DRIFT_FRAMES)


def petal_drift_clip(height: int) -> tuple[bytes, tuple[int, int]]:
    """Sakura petals drifting down over the badge on a light wind, swaying and
    turning over, for the whole ending (`height` is the badge height)."""
    size = clip_size(height)
    return encode.encode_rgba_clip(drift_frames(height), size), size
