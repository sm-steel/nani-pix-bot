"""Shatter reveal, part 1: a short shake and a white impact flash, then the stage-1 mosaic's own
blocks are knocked loose from the centre outwards, tumbling in fake 3D (edge-on squash and
darkening), growing toward the camera and falling under gravity, until the clear image shows.

The coarsest stage is literally a grid of flat-colour blocks, so a flying piece never needs its own
bitmap: it is "fill colour C through rotated mask M". Masks depend only on (block size, angle,
tumble, depth scale), quantized, so each is built once and shared by every piece. Blocks still
attached are never redrawn: a persistent base gets the clear image pasted into a block's hole the
moment it detaches, so each frame is a base copy plus the pieces in flight. Frames are rendered
straight into yuv420p planes (BT.601 limited range, what ffmpeg itself would convert to): each
piece is one 1-bit mask paste into Y and a half-size one into U and V.
"""

import math
import random
from collections.abc import Iterator
from typing import NamedTuple, cast

from PIL import Image, ImageChops

from nani_pix_bot.services.reveal import encode

CRF = 26
GRID_W = 64  # the stage-1 width: one piece per mosaic block
SEED = 7
GRAVITY = 1.1  # px/frame^2, tuned at 30 fps
ANGLE_STEPS = 36
TUMBLE_STEPS = 8
SCALE_STEPS = 4
MAX_SCALE = 0.3
SHAKE = (5, -4, 3, -2)  # anticipation: horizontal offsets, one frame each
FLASH_FRAMES = 6

# BT.601 limited range, as RGB->RGB matrices (out = a*R + b*G + c*B + d) giving Y, Cb, Cr channels
_YUV_MATRIX = (
    65.481 / 255, 128.553 / 255, 24.966 / 255, 16.0,
    -37.797 / 255, -74.203 / 255, 112.0 / 255, 128.0,
    112.0 / 255, -93.786 / 255, -18.214 / 255, 128.0,
)  # fmt: skip
_FLASH_LUT = [
    [round(v * 0.75 * (1 - i / FLASH_FRAMES) ** 2) for v in range(256)] for i in range(FLASH_FRAMES)
]

_YUV = tuple[int, int, int]
_Planes = tuple[Image.Image, Image.Image, Image.Image]
_Box = tuple[int, int, int, int]
_Mask = tuple  # (luma mask core, w, h, chroma mask core, cw, ch)


class _Piece(NamedTuple):
    start: float  # frame the shockwave reaches it
    box: _Box
    px: float
    py: float
    vx: float
    vy: float
    spin: float  # angle steps per frame
    trate: float  # tumble, rad/frame
    tphase: float
    colors: list[_YUV]  # one YUV colour per tumble step (edge-on faces go darker)


class _Flight(NamedTuple):
    """Constants of the in-flight loop for one render."""

    table: list
    half_g: float
    sc_tab: list[int]
    bounds: _Box  # xmin, xmax, ymax, ymin
    block: tuple[int, int]


def _shaded_yuv(colors: list[tuple[int, int, int]], shades: list[float]) -> list[list[_YUV]]:
    """For every rgb colour, its YUV at every shade, as one batched Pillow conversion instead of
    len(colors)*len(shades) Python-level ones. Shading truncates like int(v * k)."""
    strip = Image.new("RGB", (len(colors), 1))
    strip.putdata(colors)
    rows: list[list[_YUV]] = []
    for k in shades:
        shaded = strip.point(lambda v, k=k: int(v * k))
        rows.append(
            cast("list[_YUV]", list(shaded.convert("RGB", _YUV_MATRIX).get_flattened_data()))
        )
    return [[rows[s][i] for s in range(len(shades))] for i in range(len(colors))]


def _planes(rgb: Image.Image) -> _Planes:
    """RGB image -> (Y full, U half, V half) L planes, BT.601 limited range."""
    y, u, v = rgb.convert("RGB", _YUV_MATRIX).split()
    return y, u.reduce(2), v.reduce(2)


def _build_pieces(front: Image.Image, size: tuple[int, int]) -> list[_Piece]:
    """One piece per mosaic block, ordered by when the shockwave reaches it."""
    rng = random.Random(SEED)  # noqa: S311 - visual randomness, seeded for a reproducible look
    w, h = size
    cols, rows = GRID_W, max(1, round(h * GRID_W / w))
    cx, cy = w / 2, h / 2
    diag = math.hypot(cx, cy)
    shades = [0.55 + 0.45 * (tb + 1) / TUMBLE_STEPS for tb in range(TUMBLE_STEPS)]
    raw, rgbs = [], []
    for r in range(rows):
        for c in range(cols):
            x0, y0 = c * w // cols, r * h // rows
            x1, y1 = (c + 1) * w // cols, (r + 1) * h // rows
            px, py = (x0 + x1) / 2, (y0 + y1) / 2
            dx, dy = px - cx, py - cy
            dist = math.hypot(dx, dy) / diag
            norm = math.hypot(dx, dy) or 1.0
            speed = rng.uniform(5, 12) * (1.25 - dist) * 30 / encode.FPS
            rgbs.append(front.getpixel((int(px), int(py))))
            start = encode.FPS * (0.6 * dist + rng.uniform(0, 0.2))
            vx = dx / norm * speed
            vy = dy / norm * speed - rng.uniform(3, 9) * 30 / encode.FPS
            spin = math.radians(rng.uniform(-14, 14) * 30 / encode.FPS)
            trate = math.radians(rng.uniform(4, 16) * 30 / encode.FPS)
            tphase = rng.uniform(0, math.tau)
            spin_steps = spin * ANGLE_STEPS / math.tau
            raw.append((start, (x0, y0, x1, y1), px, py, vx, vy, spin_steps, trate, tphase))
    pieces = [_Piece(*r, colors=c) for r, c in zip(raw, _shaded_yuv(rgbs, shades), strict=True)]
    return sorted(pieces, key=lambda p: p.start)


_MASKS: dict[tuple[int, int], list] = {}


def _mask(bw: int, bh: int, a: int, tb: int, sc: int) -> _Mask:
    """The 1-bit masks (luma and half-size chroma) for one quantized piece pose."""
    tumble = (tb + 1) / TUMBLE_STEPS
    scale = 1 + MAX_SCALE * sc / (SCALE_STEPS - 1)
    mw = max(1, round(bw * scale * tumble))
    mh = max(1, round(bh * scale))
    m = Image.new("1", (mw, mh), 1).rotate(a * 360 / ANGLE_STEPS, expand=True)
    c = m.resize(((m.width + 1) // 2, (m.height + 1) // 2), Image.Resampling.NEAREST)
    m.load()
    c.load()
    # .im: private Pillow API, verified on 12.3.0; covered by tests. Pasting through the core skips
    # Image.paste's per-call Python overhead, which dominates with ~2000 pieces a frame.
    return m.im, m.width, m.height, c.im, c.width, c.height


def _mask_table(bw: int, bh: int) -> list:
    """[sc][tb][a] -> mask tuple, built lazily per block size and kept for the process."""
    t = _MASKS.get((bw, bh))
    if t is None:
        t = _MASKS[(bw, bh)] = [
            [[None] * ANGLE_STEPS for _ in range(TUMBLE_STEPS)] for _ in range(SCALE_STEPS)
        ]
    return t


def _fly(flying: list[_Piece], f: int, env: _Flight, cores: tuple) -> list[_Piece]:
    """Paint every piece still on screen at frame f into the (Y, U, V) cores; return the pieces
    that haven't left it for good."""
    yim, uim, vim = cores
    xmin, xmax, ymax, ymin = env.bounds
    bw, bh = env.block
    top_tb = TUMBLE_STEPS - 1
    cos = math.cos
    alive: list[_Piece] = []
    keep = alive.append
    for p in flying:
        t = f - p.start
        x = p.px + p.vx * t
        y = p.py + p.vy * t + env.half_g * t * t
        if y > ymax or x < xmin or x > xmax:
            continue
        keep(p)
        if y < ymin:
            continue
        tb = min(top_tb, int(abs(cos(p.tphase + p.trate * t)) * TUMBLE_STEPS))
        a = int(p.spin * t) % ANGLE_STEPS
        sc = env.sc_tab[int(t)]
        row = env.table[sc][tb]
        m = row[a]
        if m is None:
            m = row[a] = _mask(bw, bh, a, tb, sc)
        mim, mw, mh, mcim, cw, ch = m
        ix, iy = int(x) - (mw >> 1), int(y) - (mh >> 1)
        yc, uc, vc = p.colors[tb]
        yim.paste(yc, (ix, iy, ix + mw, iy + mh), mim)
        jx, jy = ix >> 1, iy >> 1
        cb = (jx, jy, jx + cw, jy + ch)
        uim.paste(uc, cb, mcim)
        vim.paste(vc, cb, mcim)
    return alive


class _Flash:
    """The white impact flash: radial falloff from the centre, decaying over FLASH_FRAMES."""

    def __init__(self, size: tuple[int, int]) -> None:
        w, h = size
        radial = Image.radial_gradient("L").resize(size, Image.Resampling.BILINEAR)
        self.y = ImageChops.invert(radial).point(lambda v: max(0, v - 90) * 255 // 165)
        self.c = self.y.reduce(2)
        # white = Y 235, U/V 128 in limited range
        self.white_y = Image.new("L", size, 235)
        self.white_c = Image.new("L", (w // 2, h // 2), 128)

    def apply(self, f: int, planes: _Planes) -> _Planes:
        y, u, v = planes
        lut = _FLASH_LUT[f]
        alpha = self.c.point(lut)
        return (
            Image.composite(self.white_y, y, self.y.point(lut)),
            Image.composite(self.white_c, u, alpha),
            Image.composite(self.white_c, v, alpha),
        )


def _emit(planes: _Planes) -> tuple[bytes, bytes, bytes]:
    y, u, v = planes
    return y.tobytes(), u.tobytes(), v.tobytes()


class _Base:
    """The persistent still-attached image (the mosaic) and the clear image, as yuv420p planes."""

    def __init__(self, front: Image.Image, original: Image.Image) -> None:
        y, u, v = _planes(front)
        self.planes: _Planes = (y.copy(), u.copy(), v.copy())
        self.clear = _planes(original)

    def detach(self, box: _Box) -> None:
        """Let the clear image show through a block's hole (luma box, and its half-size chroma)."""
        cbox = (box[0] >> 1, box[1] >> 1, box[2] >> 1, box[3] >> 1)
        for i, (dst, src) in enumerate(zip(self.planes, self.clear, strict=True)):
            b = box if i == 0 else cbox
            # .im: private Pillow API, verified on 12.3.0; covered by tests
            dst.im.paste(src.im.crop(b), b)

    def shaken(self, off: int) -> _Planes:
        y, u, v = self.planes
        return (
            ImageChops.offset(y, off, 0),
            ImageChops.offset(u, off // 2, 0),
            ImageChops.offset(v, off // 2, 0),
        )

    def frame(self) -> _Planes:
        y, u, v = (p.copy() for p in self.planes)
        return y, u, v


def frames(front: Image.Image, original: Image.Image) -> Iterator[tuple[bytes, bytes, bytes]]:
    """yuv420p frames as (Y, U, V) bytes: the mosaic, a 4-frame shake, then the flash and shatter,
    ending on the first fully clear frame. `front` is the mosaic at the original's size."""
    w, h = original.size
    pieces = _build_pieces(front, original.size)
    bw, bh = round(w / GRID_W), round(h / max(1, round(h * GRID_W / w)))
    base = _Base(front, original)
    flash = _Flash(original.size)
    env = _Flight(
        table=_mask_table(bw, bh),
        half_g=0.5 * GRAVITY * (30 / encode.FPS) ** 2,
        sc_tab=[min(SCALE_STEPS - 1, int(i / (encode.FPS * 0.25))) for i in range(encode.FPS * 30)],
        bounds=(-40, w + 40, h + 40, -2 * max(bw, bh)),
        block=(bw, bh),
    )

    yield _emit(base.planes)
    for off in SHAKE:
        yield _emit(base.shaken(off))

    n = len(pieces)
    clear = _emit(base.clear)
    nxt, flying, f = 0, [], 0
    while True:
        while nxt < n and pieces[nxt].start <= f:  # detach: the clear image shows through the hole
            base.detach(pieces[nxt].box)
            flying.append(pieces[nxt])
            nxt += 1
        planes = base.frame()
        flying = _fly(flying, f, env, tuple(p.im for p in planes))  # private Pillow API (.im)
        if f < FLASH_FRAMES:
            planes = flash.apply(f, planes)
        data = _emit(planes)
        yield data
        # all pieces released and the rest off-screen: this is the first fully clear frame, so
        # stop there (flying pieces can linger invisibly in the margin, which would repeat it)
        if nxt == n and data == clear:
            return
        f += 1


def render_part1(original: Image.Image, stages: list[Image.Image]) -> bytes:
    """Part 1 MP4: the first stage shatters away, ending on the clear frame."""
    front = stages[0]
    if front.size != original.size:
        front = front.resize(original.size, Image.Resampling.NEAREST)

    def planes() -> Iterator[bytes]:
        for frame in frames(front, original):
            yield from frame

    options = encode.Part1Options(pix_fmt="yuv420p", crf=CRF)
    return encode.encode_part1(planes(), original.size, options)
