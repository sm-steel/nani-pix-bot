"""Halloween spirit win overlays for the reveal ending (seasons spec §7): bats
bursting out of the winner's badge, as a qtrle/argb .mov like petals.sakura_storm_clip.
Runs in the reveal worker: module-level functions, seeded, no logging."""

import math
import random
from dataclasses import dataclass

from PIL import ImageDraw

from nani_pix_bot.services.reveal import encode
from nani_pix_bot.services.reveal.overlay_canvas import SS, clip_size, render

BAT_FRAMES = 56  # the ending is 60 frames; the bats fade out just before it ends
BATS = 16
BAT_SEED = 53
BAT = (44, 22, 60)
BAT_RIM = (255, 140, 40)
BAT_BURST_TAU = 5.0  # frames for the burst to slow to a third
BAT_FLAP = 0.9  # radians of wing phase per frame


@dataclass(frozen=True)
class _Bat:
    """One bat: bursts out of the badge, then flutters up and away in a zig-zag."""

    x: float  # where it bursts from
    y: float
    vx: float  # burst velocity
    vy: float
    climb: float  # how fast it flies up once the burst is spent
    zig: float  # zig-zag amplitude
    zig_speed: float
    phase: float  # wing-flap phase
    span: float  # half the wingspan


def _bats(height: int, count: int, span: tuple[float, float], seed: int) -> list[_Bat]:
    rng = random.Random(seed)  # noqa: S311 - decoration, seeded for a reproducible look
    _w, h = clip_size(height)
    out = []
    for _ in range(count):
        heading = math.radians(rng.uniform(-165, -15))  # up and out of the badge
        speed = rng.uniform(0.25, 0.55) * height
        out.append(
            _Bat(
                x=rng.uniform(0.6 * height, 4.2 * height),
                y=h - 0.5 * height,
                vx=math.cos(heading) * speed,
                vy=math.sin(heading) * speed,
                climb=rng.uniform(0.018, 0.034) * height,
                zig=rng.uniform(0.2, 0.45) * height,
                zig_speed=rng.uniform(0.18, 0.3),
                phase=rng.uniform(0, math.tau),
                span=rng.uniform(*span) * height,
            )
        )
    return out


def _pose(b: _Bat, f: int) -> tuple[float, float]:
    """Where the bat is in frame `f`, in clip pixels."""
    burst = 1 - math.exp(-f / BAT_BURST_TAU)  # the puff slows as it spreads
    return (
        b.x + b.vx * BAT_BURST_TAU * burst + b.zig * math.sin(b.phase + b.zig_speed * f),
        b.y + b.vy * BAT_BURST_TAU * burst - b.climb * f,
    )


def _flap(phase: float, f: int) -> float:
    """1 with the wings fully up, 0 at the turn."""
    return abs(math.sin(phase + BAT_FLAP * f))


def _wing(
    cx: float, cy: float, s: float, side: int, flap: float
) -> tuple[list[tuple[float, float]], list[tuple[float, float]]]:
    """One wing as (the top edge from shoulder to tip, the whole outline), `side` -1 or 1."""
    tip_x, tip_y = 0.55 + 0.45 * flap, -0.6 * flap + 0.3 * (1 - flap)

    def pt(u: float, v: float) -> tuple[float, float]:
        return cx + side * u * s, cy + v * s

    top = [pt(0.1, -0.12), pt(tip_x * 0.5, tip_y * 0.55 - 0.12), pt(tip_x, tip_y)]
    scallops = [
        pt(tip_x * 0.8, tip_y * 0.75 + 0.3),
        pt(tip_x * 0.6, tip_y * 0.5 + 0.2),
        pt(tip_x * 0.4, tip_y * 0.3 + 0.32),
        pt(tip_x * 0.22, 0.28),
        pt(0.1, 0.22),
    ]
    return top, [*top, *scallops]


def draw_bat(
    draw: ImageDraw.ImageDraw, at: tuple[float, float], span: float, flap: float, alpha: int
) -> None:
    """A bat seen from the front, centred on `at` (clip pixels): body, two scalloped wings
    whose tips rise and fall with `flap`, and an orange rim along the top of each wing."""
    cx, cy, s = at[0] * SS, at[1] * SS, span * SS
    body = (*BAT, alpha)
    for side in (-1, 1):
        top, outline = _wing(cx, cy, s, side, flap)
        draw.polygon(outline, fill=body)
        draw.line(top, fill=(*BAT_RIM, alpha), width=max(SS, round(0.07 * s)), joint="curve")
    torso = [(0, -0.3), (0.1, -0.2), (0.12, 0.1), (0.06, 0.38), (0, 0.3)]
    draw.polygon(
        [(cx + u * s, cy + v * s) for u, v in torso]
        + [(cx - u * s, cy + v * s) for u, v in reversed(torso)],
        fill=body,
    )
    for side in (-1, 1):  # ears
        ear = [(0.02, -0.28), (0.1, -0.5), (0.13, -0.2)]
        draw.polygon([(cx + side * u * s, cy + v * s) for u, v in ear], fill=body)


def bat_frames(height: int) -> bytes:
    size = clip_size(height)
    bats = _bats(height, BATS, (0.25, 0.45), BAT_SEED)

    def draw_frame(draw: ImageDraw.ImageDraw, f: int, alpha: int) -> None:
        for b in bats:
            draw_bat(draw, _pose(b, f), b.span, _flap(b.phase, f), alpha)

    return render(size, height, BAT_FRAMES, draw_frame)


def bat_burst_clip(height: int) -> tuple[bytes, tuple[int, int]]:
    """Bats burst out of the badge, then flutter up and away in loose zig-zags."""
    size = clip_size(height)
    return encode.encode_rgba_clip(bat_frames(height), size), size
