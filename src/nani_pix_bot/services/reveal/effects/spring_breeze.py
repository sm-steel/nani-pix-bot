"""Spring breeze reveal, part 1 (Sakura Season, seasons spec §7): a wind front
sweeps the pixelated image from left to right; as it reaches each block, the
block lifts off as a sakura petal in its own colour, blushing pink as it flies
off on the wind, and the clear image shows where it was.

Frames are RGB: the clear and pixelated images composited through a block-grid
mask (cheap: built at grid size, stretched NEAREST), with the flying petals
drawn on top. Seeded, so the same image always blows away the same way."""

import math
import random
from collections.abc import Iterator
from dataclasses import dataclass

from PIL import Image, ImageDraw

from nani_pix_bot.services.reveal import encode
from nani_pix_bot.services.reveal.effects._common import block_grid, rgb_at
from nani_pix_bot.services.shapes import petal_outline

SWEEP_S = 1.3  # the wind front crosses the image
FLIGHT_S = 0.7  # a petal's life after it lifts off
TILT = 0.35  # the front leans: lower rows go a little later
JITTER = 0.08  # of the sweep: blocks don't all leave on a perfect line
PETAL_SHARE = 0.6  # of the blocks, this many become a visible petal
PINK = (255, 183, 205)
SEED = 17
CRF = 23


@dataclass(frozen=True)
class _Flyer:
    x: float  # where it lifted off (block centre)
    y: float
    leaves: float  # seconds into the sweep
    color: tuple[int, int, int]
    vx: float  # px/s
    vy: float
    spin: float
    angle: float
    size: float


def _departures(grid: tuple[int, int], rng: random.Random) -> list[list[float]]:
    """When each block leaves, in seconds into the sweep."""
    gw, gh = grid
    span = 1 + TILT
    return [
        [
            max(
                0.0, ((gx + 0.5) / gw + TILT * (gy + 0.5) / gh) / span + rng.uniform(-1, 1) * JITTER
            )
            * SWEEP_S
            for gx in range(gw)
        ]
        for gy in range(gh)
    ]


def _flyers(
    front: Image.Image, grid: tuple[int, int], leaves: list[list[float]], rng: random.Random
) -> list[_Flyer]:
    w, h = front.size
    gw, gh = grid
    colours = front.resize(grid, Image.Resampling.BOX).convert("RGB").tobytes()
    bw, bh = w / gw, h / gh
    out = []
    for gy in range(gh):
        for gx in range(gw):
            if rng.random() > PETAL_SHARE:
                continue
            heading = math.radians(rng.uniform(-35, 5))  # right, a little up
            speed = rng.uniform(0.35, 0.7) * w
            out.append(
                _Flyer(
                    x=(gx + 0.5) * bw,
                    y=(gy + 0.5) * bh,
                    leaves=leaves[gy][gx],
                    color=rgb_at(colours, (gy * gw + gx) * 3),
                    vx=math.cos(heading) * speed,
                    vy=math.sin(heading) * speed,
                    spin=rng.uniform(-9, 9),
                    angle=rng.uniform(0, math.tau),
                    size=max(bw, bh) * rng.uniform(0.9, 1.3),
                )
            )
    return out


def _mask(grid: tuple[int, int], leaves: list[list[float]], t: float, size: tuple[int, int]):
    """255 where the block has already blown away (the clear image shows)."""
    gw, gh = grid
    small = Image.new("L", grid, 0)
    small.putdata([255 if leaves[gy][gx] <= t else 0 for gy in range(gh) for gx in range(gw)])
    return small.resize(size, Image.Resampling.NEAREST)


def _blend(a: tuple[int, int, int], b: tuple[int, int, int], k: float) -> tuple[int, int, int]:
    r, g, bl = (round(x + (y - x) * k) for x, y in zip(a, b, strict=True))
    return (r, g, bl)


def _draw_flyer(draw: ImageDraw.ImageDraw, p: _Flyer, t: float) -> None:
    age = t - p.leaves
    if age < 0 or age > FLIGHT_S:
        return
    k = age / FLIGHT_S
    # the gust picks up: speed grows as it flies
    x = p.x + p.vx * age * (1 + k)
    y = p.y + p.vy * age * (1 + k) - 0.15 * p.size * math.sin(k * math.pi)
    length = p.size * (1 - 0.6 * k)
    flutter = 0.3 + 0.7 * abs(math.cos(p.angle + p.spin * age))
    color = _blend(p.color, PINK, min(1.0, k * 1.6))
    outline = petal_outline((x, y), length, length * 0.42 * flutter, p.angle + p.spin * age)
    alpha = round(255 * (1 - k) ** 1.3)
    draw.polygon(outline, fill=(*color, alpha))


def frames(original: Image.Image, stages: list[Image.Image]) -> Iterator[bytes]:
    clear = original.convert("RGB")
    front = stages[0].convert("RGB")
    size = clear.size
    rng = random.Random(SEED)  # noqa: S311 - visual randomness, seeded for a reproducible look
    grid = block_grid(size)
    leaves = _departures(grid, rng)
    flyers = _flyers(front, grid, leaves, rng)
    total = SWEEP_S + FLIGHT_S
    count = math.ceil(total * encode.FPS) + 1
    for i in range(count):
        t = min(total, i / encode.FPS)
        frame = Image.composite(clear, front, _mask(grid, leaves, t, size)).convert("RGBA")
        layer = Image.new("RGBA", size, (0, 0, 0, 0))
        draw = ImageDraw.Draw(layer)
        for p in flyers:
            _draw_flyer(draw, p, t)
        frame.alpha_composite(layer)
        yield frame.convert("RGB").tobytes()
    yield clear.tobytes()  # end exactly on the clear frame


def render_part1(
    original: Image.Image, stages: list[Image.Image], mascot: encode.Still | None = None
) -> bytes:
    """Part 1 MP4: the pixelated front blowing away as sakura petals, ending on the clear image."""
    options = encode.Part1Options(crf=CRF, mascot=mascot)
    return encode.encode_part1(frames(original, stages), original.size, options)
