"""Gashadokuro rise reveal, part 1 (Yōkai Night, seasons spec): a giant skeleton's hand rises
from below the bottom edge and sweeps across the pixelated image like an arm pivoting at a
point far below it, left to right. Every block the hand's centre line has passed is cleared
(the clear image shows) and flung off along the sweep as a square in its own colour,
tumbling and fading. Then the hand sinks back out of sight.

The clearing is a block-grid mask (built at grid size, stretched NEAREST), the hand a sprite
rotated once per whole degree at quarter scale and stretched back up when pasted. A missing
hand sprite never breaks a reveal: the blocks clear along the sweep line without it (the gap
is logged once, see `_common.sprite_or_none`). Seeded, so the same image always crumbles the
same way."""

import math
import random
from bisect import bisect_left, bisect_right
from collections.abc import Iterator
from dataclasses import dataclass

from PIL import Image, ImageDraw

from nani_pix_bot.services.reveal import encode
from nani_pix_bot.services.reveal.effects._common import ease_in_out, sprite_or_none
from nani_pix_bot.services.reveal.effects.spring_breeze import _grid, _rgb_at

RISE_S = 0.4
SWEEP_S = 1.1
SINK_S = 0.35
FLING_S = 0.5
FADE_STEPS = 8  # a square fades through this many colours
FLING_SHARE = 0.5  # of the blocks, this many get a visible flying square
HAND = "skeleton_hand.png"
HAND_HEIGHT = 1.1  # of the image's height
PIVOT = (0.5, 1.35)  # of the width / height: well below the bottom edge
SWEEP_DEG = 62.0  # the hand turns from -SWEEP_DEG to +SWEEP_DEG about the pivot
HIDDEN = 1.3  # of the hand's length: how far back it is pulled to be out of sight
SMALL = 4  # the sprite is rotated at 1/SMALL scale
MIN_HAND_PX = 8
SEED = 47
CRF = 23


@dataclass(frozen=True)
class _Flyer:
    x: float  # the block's centre
    y: float
    leaves: float  # seconds into the animation
    fades: tuple[tuple[int, int, int], ...]  # its colour from the front to the clear image
    vx: float  # px/s
    vy: float
    spin: float
    angle: float
    size: float


@dataclass(frozen=True)
class _Hand:
    """The sprite at 1/SMALL scale (wrist at the bottom centre), `length` its full-size
    height; `turned` caches its rotations by whole degree as (colour, alpha)."""

    art: Image.Image
    length: float
    turned: dict[int, tuple[Image.Image, Image.Image]]


def _pivot(size: tuple[int, int]) -> tuple[float, float]:
    return PIVOT[0] * size[0], PIVOT[1] * size[1]


def _total() -> float:
    return RISE_S + SWEEP_S + SINK_S


def _angle(size: tuple[int, int], x: float, y: float) -> float:
    """Degrees from straight up, about the pivot, positive to the right."""
    px, py = _pivot(size)
    return math.degrees(math.atan2(x - px, py - y))


def _departures(size: tuple[int, int], grid: tuple[int, int]) -> list[list[float]]:
    """When the hand's centre line passes each block, in seconds into the animation."""
    w, h = size
    gw, gh = grid
    out = []
    for gy in range(gh):
        row = []
        for gx in range(gw):
            turn = _angle(size, (gx + 0.5) * w / gw, (gy + 0.5) * h / gh)
            share = (min(SWEEP_DEG, max(-SWEEP_DEG, turn)) + SWEEP_DEG) / (2 * SWEEP_DEG)
            row.append(RISE_S + share * SWEEP_S)
        out.append(row)
    return out


def _flyers(
    clear: Image.Image,
    front: Image.Image,
    grid: tuple[int, int],
    leaves: list[list[float]],
    rng: random.Random,
) -> list[_Flyer]:
    w, h = front.size
    gw, gh = grid
    colours = front.resize(grid, Image.Resampling.BOX).tobytes()
    clear_colours = clear.resize(grid, Image.Resampling.BOX).tobytes()
    bw, bh = w / gw, h / gh
    out = []
    for gy in range(gh):
        for gx in range(gw):
            if rng.random() > FLING_SHARE:
                continue
            x, y = (gx + 0.5) * bw, (gy + 0.5) * bh
            # along the sweep: the tangent about the pivot, tilted a little up
            heading = math.radians(_angle(front.size, x, y) - rng.uniform(0, 40))
            speed = rng.uniform(0.25, 0.55) * w
            out.append(
                _Flyer(
                    x=x,
                    y=y,
                    leaves=leaves[gy][gx],
                    fades=_fades(
                        _rgb_at(colours, (gy * gw + gx) * 3),
                        _rgb_at(clear_colours, (gy * gw + gx) * 3),
                    ),
                    vx=math.cos(heading) * speed,
                    vy=math.sin(heading) * speed,
                    spin=rng.uniform(-9, 9),
                    angle=rng.uniform(0, math.tau),
                    size=max(bw, bh) * rng.uniform(0.8, 1.2),
                )
            )
    return out


def _blend(a: tuple[int, int, int], b: tuple[int, int, int], k: float) -> tuple[int, int, int]:
    r, g, bl = (round(x + (y - x) * k) for x, y in zip(a, b, strict=True))
    return (r, g, bl)


def _fades(
    front: tuple[int, int, int], clear: tuple[int, int, int]
) -> tuple[tuple[int, int, int], ...]:
    return tuple(_blend(front, clear, step / (FADE_STEPS - 1)) for step in range(FADE_STEPS))


def _draw_flyer(draw: ImageDraw.ImageDraw, p: _Flyer, age: float) -> None:
    """One square, tumbling and shrinking. It fades by taking on the colour of the clear image
    where it started rather than by alpha, which would cost a full-frame layer."""
    k = min(age / FLING_S, 0.999)
    x, y = p.x + p.vx * age, p.y + p.vy * age
    turn = p.angle + p.spin * age
    half = p.size / 2 * (1 - 0.5 * k)
    ax, ay = half * math.cos(turn), half * math.sin(turn)  # half a side, along the square
    bx, by = -ay, ax  # and across it
    corners = [
        (x + ax + bx, y + ay + by),
        (x + ax - bx, y + ay - by),
        (x - ax - bx, y - ay - by),
        (x - ax + bx, y - ay + by),
    ]
    draw.polygon(corners, fill=p.fades[int(k * FADE_STEPS)])


def _hand(size: tuple[int, int]) -> _Hand | None:
    length = round(size[1] * HAND_HEIGHT)
    if length < MIN_HAND_PX:
        return None
    art = sprite_or_none(HAND, max(1, round(length / SMALL)), "the blocks clear without the hand")
    if art is None:
        return None
    return _Hand(art, art.height * SMALL, {})


def _pose(seconds: float, length: float) -> tuple[float, float]:
    """(degrees, how far the hand is pulled back along its length, in px)."""
    if seconds < RISE_S:
        return -SWEEP_DEG, HIDDEN * length * (1 - ease_in_out(seconds / RISE_S))
    if seconds < RISE_S + SWEEP_S:
        turn = (seconds - RISE_S) / SWEEP_S
        return -SWEEP_DEG + 2 * SWEEP_DEG * turn, 0.0
    sink = min(1.0, (seconds - RISE_S - SWEEP_S) / SINK_S)
    return SWEEP_DEG, HIDDEN * length * ease_in_out(sink)


def _turned(hand: _Hand, degrees: int) -> tuple[Image.Image, Image.Image]:
    """The hand turned clockwise by whole degrees (rotated once per angle, then kept), as
    colour and alpha at half full size: stretched separately, which is cheaper than RGBA, and in
    two steps (smooth to half size here, a plain 2x at paste time), cheaper still."""
    if degrees not in hand.turned:
        turned = hand.art.rotate(-degrees, Image.Resampling.BILINEAR, expand=True)
        half = (turned.width * SMALL // 2, turned.height * SMALL // 2)
        hand.turned[degrees] = (
            turned.convert("RGB").resize(half, Image.Resampling.NEAREST),
            turned.getchannel("A").resize(half, Image.Resampling.BILINEAR),
        )
    return hand.turned[degrees]


def _paste_hand(frame: Image.Image, hand: _Hand, size: tuple[int, int], seconds: float) -> None:
    degrees, back = _pose(seconds, hand.length)
    whole = round(degrees)
    colour, alpha = _turned(hand, whole)
    px, py = _pivot(size)
    rad = math.radians(whole)
    reach = hand.length / 2 - back  # the hand's centre, along its axis from the pivot
    cx, cy = px + math.sin(rad) * reach, py - math.cos(rad) * reach
    width, height = colour.width * 2, colour.height * 2
    left, top = round(cx - width / 2), round(cy - height / 2)
    x0, y0 = max(0, left), max(0, top)
    x1, y1 = min(size[0], left + width), min(size[1], top + height)
    if x1 <= x0 or y1 <= y0:
        return
    box = ((x0 - left) / 2, (y0 - top) / 2, (x1 - left) / 2, (y1 - top) / 2)
    out = (x1 - x0, y1 - y0)
    frame.paste(
        colour.resize(out, Image.Resampling.NEAREST, box=box),
        (x0, y0),
        alpha.resize(out, Image.Resampling.NEAREST, box=box),
    )


def _edges(blocks: int, pixels: int) -> list[int]:
    """Where each block starts, as a NEAREST stretch of `blocks` onto `pixels` puts it."""
    return [min(pixels, math.ceil(i * pixels / blocks - 0.5)) for i in range(blocks + 1)]


def frames(original: Image.Image, stages: list[Image.Image]) -> Iterator[bytes]:
    clear = original.convert("RGB")
    front = stages[0].convert("RGB")
    size = clear.size
    rng = random.Random(SEED)  # noqa: S311 - visual randomness, seeded for a reproducible look
    grid = _grid(size)
    leaves = _departures(size, grid)
    flyers = _flyers(clear, front, grid, leaves, rng)
    hand = _hand(size)
    flyers.sort(key=lambda p: p.leaves)
    leave_times = [p.leaves for p in flyers]
    xs, ys = _edges(grid[0], size[0]), _edges(grid[1], size[1])
    pending = sorted((leaves[gy][gx], gx, gy) for gy in range(grid[1]) for gx in range(grid[0]))
    base = front.copy()  # the blocks the hand has passed so far, clear
    count = math.ceil(_total() * encode.FPS) + 1
    for i in range(count):
        t = min(_total(), i / encode.FPS)
        while pending and pending[0][0] <= t:
            _, gx, gy = pending.pop(0)
            box = (xs[gx], ys[gy], xs[gx + 1], ys[gy + 1])
            base.paste(clear.crop(box), box[:2])
        frame = base.copy()
        draw = ImageDraw.Draw(frame)
        first = bisect_left(leave_times, t - FLING_S)
        last = bisect_right(leave_times, t)
        for p in flyers[first:last]:
            _draw_flyer(draw, p, t - p.leaves)
        if hand is not None:
            _paste_hand(frame, hand, size, t)
        yield frame.tobytes()
    yield clear.tobytes()  # end exactly on the clear frame


def render_part1(
    original: Image.Image, stages: list[Image.Image], mascot: encode.Still | None = None
) -> bytes:
    """Part 1 MP4: a skeleton hand sweeping the pixelated image away, ending on the clear one."""
    options = encode.Part1Options(crf=CRF, mascot=mascot)
    return encode.encode_part1(frames(original, stages), original.size, options)
