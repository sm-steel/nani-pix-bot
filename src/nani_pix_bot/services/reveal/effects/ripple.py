"""Stage-ripple reveal, part 1: the coarsest stage, then each game stage ripples into the next as a
true circle from the image's "interesting point" with a soft glowing wave front, and a final,
brighter ripple reaches the clear image, which a white flash decays onto. Ends on the first fully
clear frame.

The masks depend only on the ripple progress t (and on the brighter final front), and the stage
transitions share the same t values, so each (t, boost) mask set is built once and reused. A ripple
frame's plane is `prefix + region + suffix`: the untouched rows of the old stage, the composited
full-width row band the front has reached, and the untouched rows below. Frames are built directly
as yuv420p planes (Y full size, Cb/Cr half size, BT.601 limited range), so ffmpeg does no colour
conversion.
"""

import math
from array import array
from collections.abc import Iterator
from typing import NamedTuple, cast

from PIL import Image, ImageMath

from nani_pix_bot.services.reveal import encode
from nani_pix_bot.services.reveal.effects._common import ease_in_out

CRF = 21
BAND, GLOW, GLOW_BOOST = 0.16, 120, 40
RIPPLE_S, LAST_RIPPLE_S, STILL_S = 0.4, 0.55, 0.25
FLASH_FRAMES = 9  # the last one is exactly the clear image
_WHITE = (235, 128, 128)  # limited-range white per plane

# BT.601 full range (Pillow YCbCr) -> limited range (ffmpeg's default rgb24 -> yuv420p)
_Y_LUT = [round(16 + v * 219 / 255) for v in range(256)]
_C_LUT = [round(128 + (v - 128) * 224 / 255) for v in range(256)]

_Frame = tuple[bytes, bytes, bytes]


class _Step(NamedTuple):
    """One entry of the timeline: `kind` is "still", "ripple" or "flash"."""

    kind: str
    a: int  # still: the stage; ripple: the stage it covers; flash: the flash index
    b: int = 0  # ripple: the stage it reveals
    t: float = 0.0  # ripple: eased progress
    last: bool = False  # ripple into the clear image (brighter front)
    repeat: int = 1


class _Band(NamedTuple):
    """One plane's share of a ripple mask set: the row band, its masks and the white to paint."""

    box: tuple[int, int, int, int]
    alpha: Image.Image
    ring: Image.Image
    white: Image.Image


class _Planes:
    """One image as yuv420p planes (Y full res, Cb/Cr half res), limited range, + raw bytes."""

    def __init__(self, img: Image.Image) -> None:
        # Not `_common.to_yuv420`: its rounding differs, and this conversion is byte-identical to
        # the approved spike output. Chroma is averaged 2x2 in full range first, then mapped to
        # limited range.
        y, cb, cr = img.convert("YCbCr").split()
        self.p = (y.point(_Y_LUT), cb.reduce(2).point(_C_LUT), cr.reduce(2).point(_C_LUT))
        self.raw = (self.p[0].tobytes(), self.p[1].tobytes(), self.p[2].tobytes())


def _timeline(n_stages: int, fps: int) -> list[_Step]:
    """The whole animation: the opening still, then per stage a ripple (and a hold unless it is
    the last), then the flash frames."""
    steps = [_Step("still", 0)]
    for k in range(n_stages):
        last = k == n_stages - 1
        n = round(fps * (LAST_RIPPLE_S if last else RIPPLE_S))
        steps += [_Step("ripple", k, k + 1, ease_in_out(i / n), last) for i in range(1, n + 1)]
        if not last:
            steps.append(_Step("still", k + 1, repeat=round(fps * STILL_S)))
    steps += [_Step("flash", i) for i in range(1, FLASH_FRAMES + 1)]
    return steps


def _box_mean(img: Image.Image, box: tuple[int, int, int, int]) -> float:
    """Mean of an F image over `box` (a BOX resize to one pixel, in C)."""
    return cast(float, img.crop(box).resize((1, 1), Image.Resampling.BOX).getpixel((0, 0)))


def interesting_point(img: Image.Image, cells: int = 12) -> tuple[int, int]:
    """Centre of the most detailed cell (sum of per-channel stddevs, pulled toward the image
    centre) on a coarse grid over a 96 px thumbnail. Per-cell mean and variance come from
    BOX-resizes of exact cell crops of the thumbnail and of its square, all in C."""
    small = img.resize((96, max(1, round(96 * img.height / img.width))), Image.Resampling.BOX)
    sw, sh = small.size
    cw, ch = sw / cells, sh / cells
    chans = [c.convert("F") for c in small.split()]
    squares = [ImageMath.lambda_eval(lambda a: a["c"] * a["c"], c=c) for c in chans]
    best, pt = -1.0, (sw / 2, sh / 2)
    for r in range(cells):
        for c in range(cells):
            box = (round(c * cw), round(r * ch), round((c + 1) * cw), round((r + 1) * ch))
            if box[2] <= box[0] or box[3] <= box[1]:
                continue
            score = 0.0
            for chan, sq in zip(chans, squares, strict=True):
                mean, msq = _box_mean(chan, box), _box_mean(sq, box)
                score += math.sqrt(max(0.0, msq - mean * mean))
            mx, my = (box[0] + box[2]) / 2, (box[1] + box[3]) / 2
            score *= 1 - 0.8 * math.hypot(mx / sw - 0.5, my / sh - 0.5)
            if score > best:
                best, pt = score, (mx, my)
    return round(pt[0] * img.width / sw), round(pt[1] * img.height / sh)


def _distance_maps(
    size: tuple[int, int], origin: tuple[int, int], far: float
) -> tuple[Image.Image, Image.Image]:
    """(full-res, half-res) L distance fields, 255 = farthest corner. The exact float field is
    computed once at half resolution (a quarter of the sqrt work) and BILINEAR-upscaled in float
    before quantising: distance is linear away from the origin, so this matches the exact field
    to within the 8-bit rounding the masks already have."""
    w, h = size
    hw, hh = w // 2, h // 2
    ox, oy = (origin[0] - 0.5) / 2, (origin[1] - 0.5) / 2  # pixel-centre aligned half-res origin
    xs = Image.frombytes("F", (hw, 1), array("f", (x - ox for x in range(hw))).tobytes())
    ys = Image.frombytes("F", (1, hh), array("f", (y - oy for y in range(hh))).tobytes())
    xs = xs.resize((hw, hh), Image.Resampling.NEAREST)
    ys = ys.resize((hw, hh), Image.Resampling.NEAREST)
    k = 2 * 255 / far
    half = ImageMath.lambda_eval(
        lambda a: (a["x"] * a["x"] + a["y"] * a["y"]) ** 0.5 * k, x=xs, y=ys
    )
    full = half.resize((w, h), Image.Resampling.BILINEAR).convert("L")
    return full, full.resize((hw, hh), Image.Resampling.BOX)


def _ripple_luts(t: float, band: float, glow: int) -> tuple[list[int], list[int]]:
    """256-entry LUTs over the distance field: alpha (how much of the new stage shows) and the
    white glow ring on the wave front at progress t."""
    front = t * (1 + band)
    lo = front - band
    alpha, ring = [], []
    for v in range(256):
        d = v / 255
        if d <= lo:
            alpha.append(255)
        elif d >= front:
            alpha.append(0)
        else:
            alpha.append(round(255 * (front - d) / band))
        ring.append(round(glow * math.sin(math.pi * (d - lo) / band)) if lo <= d <= front else 0)
    return alpha, ring


class _Ripple:
    """Everything a ripple frame needs: the distance fields, the white planes and the (t, boost)
    mask sets, built on first use and shared across every transition with the same t."""

    def __init__(self, size: tuple[int, int], origin: tuple[int, int], white: list[Image.Image]):
        w, h = size
        self.height = h
        self.origin_y = origin[1]
        self.far = max(math.hypot(origin[0] - cx, origin[1] - cy) for cx in (0, w) for cy in (0, h))
        full, half = _distance_maps(size, origin, self.far)
        self.dist = (full, half, half)
        self.white = white
        self._masks: dict[tuple[float, bool], list[_Band]] = {}

    def mask_set(self, t: float, boost: bool) -> list[_Band]:
        """Row band (even-aligned) reached by the front at t, and per-plane alpha/ring masks
        cropped to it."""
        key = (t, boost)
        if key not in self._masks:
            alpha, ring = _ripple_luts(t, BAND, GLOW + GLOW_BOOST if boost else GLOW)
            r = math.ceil(t * (1 + BAND) * self.far) + 2
            y0 = max(0, (self.origin_y - r) // 2 * 2)
            y1 = min(self.height, -(-(self.origin_y + r) // 2) * 2)
            bands = []
            for i, d in enumerate(self.dist):
                s = 1 if i == 0 else 2
                box = (0, y0 // s, d.width, y1 // s)
                dc = d.crop(box)
                bands.append(_Band(box, dc.point(alpha), dc.point(ring), self.white[i].crop(box)))
            self._masks[key] = bands
        return self._masks[key]

    def frame(self, a: _Planes, b: _Planes, t: float, boost: bool) -> _Frame:
        """Each plane = untouched rows of `a` above + composited band + untouched rows below.
        Outside the front alpha and ring are 0, so those pixels are exactly `a` either way."""
        out = []
        for i, band in enumerate(self.mask_set(t, boost)):
            region = Image.composite(b.p[i].crop(band.box), a.p[i].crop(band.box), band.alpha)
            region = Image.composite(band.white, region, band.ring)
            wd = a.p[i].width
            raw = a.raw[i]
            out.append(raw[: band.box[1] * wd] + region.tobytes() + raw[band.box[3] * wd :])
        return out[0], out[1], out[2]


def _flash(clear: _Planes, white: list[Image.Image], i: int) -> _Frame:
    """The clear image with white blended over it, decaying to nothing at i == FLASH_FRAMES."""
    k = 0.7 * (1 - i / FLASH_FRAMES) ** 2
    if k == 0:
        return clear.raw
    y, cb, cr = (Image.blend(p, w, k).tobytes() for p, w in zip(clear.p, white, strict=True))
    return y, cb, cr


def render_yuv(
    original: Image.Image, stages: list[Image.Image], origin: tuple[int, int] | None = None
) -> Iterator[_Frame]:
    """yuv420p frames as (Y, Cb, Cr) bytes, every hold repeated, ending on the clear image."""
    size = original.size
    stages = [s if s.size == size else s.resize(size, Image.Resampling.NEAREST) for s in stages]
    imgs = [_Planes(s) for s in [*stages, original]]
    white = [Image.new("L", p.size, v) for p, v in zip(imgs[0].p, _WHITE, strict=True)]
    ripple = _Ripple(size, origin or interesting_point(original), white)
    for step in _timeline(len(stages), encode.FPS):
        if step.kind == "still":
            frame = imgs[step.a].raw
        elif step.kind == "ripple":
            frame = ripple.frame(imgs[step.a], imgs[step.b], step.t, step.last)
        else:
            frame = _flash(imgs[-1], white, step.a)
        for _ in range(step.repeat):
            yield frame


def render_part1(original: Image.Image, stages: list[Image.Image]) -> bytes:
    """Part 1 MP4: coarsest stage -> a ripple per stage -> the clear image + a decaying white
    flash, ending on the clear frame."""

    def planes() -> Iterator[bytes]:
        for frame in render_yuv(original, stages):
            yield from frame

    options = encode.Part1Options(pix_fmt="yuv420p", crf=CRF)
    return encode.encode_part1(planes(), original.size, options)
