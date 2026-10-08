"""Iris reveal, part 1: a peephole pops open (with overshoot) on a face, pauses, then bursts open
with a glowing ring to the full clear image.

An iris frame is three regions: the pixelated front outside the circle, the clear image inside
it, and a soft white glow ring on the boundary. Frames are built directly as yuv420p planes: the
disc by joining row slices of front and clear bytes (plain memcpy, no masks), the ring by pasting
constant white through a small precomputed radial-alpha mask. The opening hold and the pause never
cross the pipe: a `setpts` expression spaces the unique frames and ffmpeg's constant-frame-rate
output repeats them to fill the gaps.
"""

import array
import functools
import math
from collections.abc import Iterator

from PIL import Image, ImageChops, ImageDraw, ImageFilter, ImageMath

from nani_pix_bot.services.reveal import encode
from nani_pix_bot.services.reveal.effects._common import ease_in_out, ease_out_back, to_yuv420

OPEN_S, PAUSE_S, BURST_S = 0.45, 0.6, 0.7
CRF = 23  # x264's default; the approved iris look was encoded at it (rate control isn't in the SPS)
ANALYSIS_W = 320
WHITE_Y, WHITE_C = 235, 128

# glow ring: alpha (0..255) at distance |d| px from the ring radius (peak ~0.41 white at the
# radius, ~0.24 at 5 px, fading out by ~15 px)
RING_ALPHA = [104, 101, 95, 87, 74, 61, 50, 40, 31, 24, 18, 13, 9, 6, 3, 1]
DISC_INSET = 2.0  # the disc's hard edge sits this far outside the ring centre, under the glow

_Box = tuple[int, int, int, int]
_Ring = tuple[_Box, Image.Image] | None


# --- focus: where the peephole opens (skin tone + line-art detail, i.e. a face) ---


def _lut(lo: int, hi: int) -> list[int]:
    return [255 if lo <= v <= hi else 0 for v in range(256)]


@functools.lru_cache(maxsize=8)
def _weight(aw: int, ah: int) -> Image.Image:
    """Centre weight (1 - 0.8*dist) with the outer margin zeroed, as an 'F' image. Built from a
    1-row and a 1-column image stretched with NEAREST plus one ImageMath pass (no pixel loop)."""
    mx, my = round(aw * 0.06), round(ah * 0.08)
    cols = array.array("f", [(x - aw / 2) / aw for x in range(aw)]).tobytes()
    rows = array.array("f", [(y - ah / 2) / ah for y in range(ah)]).tobytes()
    xs = Image.frombytes("F", (aw, 1), cols).resize((aw, ah), Image.Resampling.NEAREST)
    ys = Image.frombytes("F", (1, ah), rows).resize((aw, ah), Image.Resampling.NEAREST)
    weight = ImageMath.lambda_eval(
        lambda a: 1 - 0.8 * (a["x"] * a["x"] + a["y"] * a["y"]) ** 0.5, x=xs, y=ys
    )
    for box in ((0, 0, aw, my), (0, ah - my, aw, ah), (0, 0, mx, ah), (aw - mx, 0, aw, ah)):
        weight.paste(0.0, box)
    return weight


def focus_point(img: Image.Image) -> tuple[int, int]:
    """Where light skin tone and line-art detail co-occur, weighted toward the centre and with the
    outer margin (player UI, letterbox) excluded. Falls back to the centre."""
    w, h = img.size
    aw = ANALYSIS_W
    ah = max(1, round(h * aw / w))
    small = img.resize((aw, ah), Image.Resampling.BOX)
    hue, sat, val = small.convert("HSV").split()
    skin = ImageChops.multiply(
        ImageChops.multiply(hue.point(_lut(0, 28)), sat.point(_lut(25, 150))),
        val.point(_lut(150, 255)),
    )
    edges = small.convert("L").filter(ImageFilter.FIND_EDGES).point(lambda v: min(255, v * 3))
    r = max(2, aw // 64)
    edge_b = edges.filter(ImageFilter.BoxBlur(r))
    score = ImageChops.multiply(skin.filter(ImageFilter.BoxBlur(r)), edge_b)
    total = ImageMath.lambda_eval(
        lambda a: (a["s"] + a["e"] * 0.15) * a["w"],
        s=score.convert("F"),
        e=edge_b.convert("F"),
        w=_weight(aw, ah),
    )
    _, top = total.getextrema()
    if top <= 0:
        return (w // 2, h // 2)
    peak = ImageMath.lambda_eval(lambda a: a["t"] >= top, t=total).convert("L")
    bbox = peak.getbbox()
    if bbox is None:
        return (w // 2, h // 2)
    # first maximum in scan order: topmost row, leftmost pixel in it
    first = peak.crop((0, bbox[1], aw, bbox[1] + 1)).getbbox()
    if first is None:
        return (w // 2, h // 2)
    return (round(first[0] * w / aw), round(bbox[1] * h / ah))


# --- frames ---


def _row_span(cx: float, r2: float, dy: float, pw: int) -> tuple[int, int] | None:
    """Pixel columns [a, b) of one row that lie inside the circle, or None if it misses it."""
    hh = r2 - dy * dy
    if hh <= 0:
        return None
    half = math.sqrt(hh)
    a = min(pw, max(0, math.ceil(cx - half - 0.5)))
    b = min(pw, max(0, math.floor(cx + half - 0.5) + 1))
    return a, b


def disc_plane(
    front: memoryview,
    clear: memoryview,
    size: tuple[int, int],
    circle: tuple[float, float, float],
) -> bytes:
    """Plane bytes with `clear` inside the circle (cx, cy, r) and `front` outside it."""
    pw, ph = size
    cx, cy, r = circle
    if r <= 0:
        return bytes(front)
    y0 = max(0, math.ceil(cy - r - 0.5))
    y1 = min(ph, math.floor(cy + r - 0.5) + 1)
    if y1 <= y0:
        return bytes(front)
    chunks = [front[: y0 * pw]]
    for y in range(y0, y1):
        o = y * pw
        span = _row_span(cx, r * r, y + 0.5 - cy, pw)
        if span is None:
            chunks.append(front[o : o + pw])
        elif span == (0, pw):
            chunks.append(clear[o : o + pw])
        else:
            a, b = span
            chunks += (front[o : o + a], clear[o + a : o + b], front[o + b : o + pw])
    chunks.append(front[y1 * pw :])
    return b"".join(chunks)


def ring_mask(
    size: tuple[int, int], circle: tuple[float, float, float], profile: list[int]
) -> _Ring:
    """Glow-ring alpha over its clipped bounding box, as (box, L mask). Drawn as thin outlines
    from the faint outside in, so each brighter band overwrites the fainter one."""
    pw, ph = size
    cx, cy, r = circle
    reach = len(profile)
    x0, y0 = max(0, math.floor(cx - r - reach)), max(0, math.floor(cy - r - reach))
    x1, y1 = min(pw, math.ceil(cx + r + reach)), min(ph, math.ceil(cy + r + reach))
    if x1 <= x0 or y1 <= y0:
        return None
    # ring entirely outside the frame (circle encloses the whole box with margin): nothing to draw
    if r - reach > max(math.hypot(cx - x, cy - y) for x in (x0, x1) for y in (y0, y1)):
        return None
    mask = Image.new("L", (x1 - x0, y1 - y0), 0)
    draw = ImageDraw.Draw(mask)
    ox, oy = cx - x0, cy - y0
    for k in range(len(profile) - 1, -1, -1):
        for rr in (r + k, r - k) if k else (r,):
            if rr > 0:
                rad = rr + 1  # the 2 px outline is drawn inward from the bounding box
                draw.ellipse((ox - rad, oy - rad, ox + rad, oy + rad), outline=profile[k], width=2)
    return (x0, y0, x1, y1), mask


def add_ring(plane: bytes, size: tuple[int, int], ring: _Ring, value: int) -> bytes:
    """Blend constant `value` through the ring mask."""
    if ring is None:
        return plane
    box, mask = ring
    im = Image.frombytes("L", size, plane)
    im.paste(value, box, mask)
    return im.tobytes()


class IrisFrames:
    """Builds yuv420p frames of the peephole at a given radius."""

    def __init__(self, original: Image.Image, front: Image.Image, focus: tuple[int, int]) -> None:
        if front.size != original.size:
            front = front.resize(original.size, Image.Resampling.NEAREST)
        self.size = original.size
        self.front = [memoryview(p) for p in to_yuv420(front)]
        self.clear = [memoryview(p) for p in to_yuv420(original)]
        self.focus = focus

    def frame(self, radius: float) -> bytes:
        w, h = self.size
        half = (w // 2, h // 2)
        fx, fy = self.focus
        inset = radius + DISC_INSET
        yp = disc_plane(self.front[0], self.clear[0], self.size, (fx, fy, inset))
        up = disc_plane(self.front[1], self.clear[1], half, (fx / 2, fy / 2, inset / 2))
        vp = disc_plane(self.front[2], self.clear[2], half, (fx / 2, fy / 2, inset / 2))
        ring = ring_mask(self.size, (fx, fy, radius), RING_ALPHA)
        if ring is not None:
            yp = add_ring(yp, self.size, ring, WHITE_Y)
            chroma_ring = ring_mask(half, (fx / 2, fy / 2, radius / 2), RING_ALPHA[::2])
            up = add_ring(up, half, chroma_ring, WHITE_C)
            vp = add_ring(vp, half, chroma_ring, WHITE_C)
        return b"".join((yp, up, vp))

    def front_frame(self) -> bytes:
        return b"".join(self.front)

    def clear_frame(self) -> bytes:
        return b"".join(self.clear)


def _radii(size: tuple[int, int], focus: tuple[int, int]) -> tuple[list[float], int, int]:
    """Radius per unique moving frame, plus (index of the frame the pause holds, pause frames)."""
    w, h = size
    fx, fy = focus
    far = max(math.hypot(fx - cx, fy - cy) for cx in (0, w) for cy in (0, h))
    peek = h * 0.16
    open_n = int(encode.FPS * OPEN_S)
    pause_n = int(encode.FPS * PAUSE_S)
    burst_n = int(encode.FPS * BURST_S)
    open_r = [peek * ease_out_back(i / open_n) for i in range(1, open_n + 1)]
    # the last burst step is replaced by the exact clear image (part 1 ends on it)
    burst_r = [
        peek + (far * 1.05 - peek) * ease_in_out(i / burst_n) ** 1.3 for i in range(1, burst_n)
    ]
    return open_r + burst_r, open_n, pause_n


def _pts_expr(start: int, hold_idx: int, extra: int) -> str:
    """Unique frame N lands at output frame N, shifted by `start` frames after frame 0 (the
    opening hold) and by `extra` more after `hold_idx` (the pause); ffmpeg's CFR output repeats the
    previous frame to fill each gap. Replaces tpad, which mis-counted on this VFR input."""
    return f"(N+gt(N\\,0)*{start}+gt(N\\,{hold_idx})*{extra})/({encode.FPS}*TB)"


def _frames(frames: IrisFrames, radii: list[float]) -> Iterator[bytes]:
    yield frames.front_frame()
    yield from map(frames.frame, radii)
    clear = frames.clear_frame()
    yield clear  # twice: CFR output drops the stream's last frame (no next timestamp)
    yield clear


def render_part1(original: Image.Image, stages: list[Image.Image]) -> bytes:
    """Part 1 MP4: front stage -> peephole pops open on the focus -> pause -> burst to the clear
    image, ending on it."""
    focus = focus_point(original)
    radii, hold_idx, pause = _radii(original.size, focus)
    hold = round(encode.HOLD_START * encode.FPS)
    options = encode.Part1Options(
        pix_fmt="yuv420p", vf=f"setpts={_pts_expr(hold, hold_idx, pause)}", crf=CRF
    )
    frames = _frames(IrisFrames(original, stages[0], focus), radii)
    return encode.encode_part1(frames, original.size, options)
