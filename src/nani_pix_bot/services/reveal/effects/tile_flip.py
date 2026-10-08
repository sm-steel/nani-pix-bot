"""Tile-flip reveal, part 1: a diagonal wave of perspective card flips turns the pixelated front
into the clear back, ending on the clear image.

A flipping card's silhouette is a trapezoid and the face inside is only ever squashed
horizontally, so each strip is a plain resize (NEAREST for the mosaic front, antialiased
BILINEAR for the clear back) clipped by a precomputed antialiased trapezoid mask. Behind a
turning tile the pixelated image shows through darkened to 45% (the "shadow" gap), and the
turning face darkens further as it goes edge-on.

Everything is composited directly on yuv420p planes (Y full size, Cb/Cr half size, BT.601
limited range) as L images, so each frame is three cheap plane dumps and ffmpeg does no colour
conversion. Darkening is linear in RGB, so in YUV it is Y' = 16 + (Y-16)*b, C' = 128 + (C-128)*b:
one paste of the solid 16/128 through a per-step "shade" mask. All geometry is computed once per
(step, tile size) and the per-frame schedule is precomputed.
"""

import math
from collections.abc import Iterator
from typing import Any, NamedTuple

from PIL import Image, ImageDraw, ImageEnhance

from nani_pix_bot.services.reveal import encode
from nani_pix_bot.services.reveal.effects._common import ease_in_out

CRF = 21
COLS = 16
FLIP_S = 0.45  # one tile's flip
SPREAD_S = 1.8  # first tile's start to last tile's start
PERSPECTIVE = 0.18  # how much a turning tile's far edge shrinks, as a fraction of its height
SHADOW_BRIGHTNESS = 0.45  # the pixelated image behind a turning tile
SOLID = (16, 128, 128)  # limited-range black per plane
_SS = 4  # supersampling for the antialiased trapezoid masks

# BT.601 full range (Pillow YCbCr) -> limited range (ffmpeg's default rgb24 -> yuv420p)
_Y_LUT = [round(16 + v * 219 / 255) for v in range(256)]
_C_LUT = [round(128 + (v - 128) * 224 / 255) for v in range(256)]

_Planes = tuple[Image.Image, Image.Image, Image.Image]
_Frame = tuple[bytes, bytes, bytes]
_Box = tuple[int, int, int, int]


class _Geo(NamedTuple):
    """One flip step of one tile size: strip width, x offset, cover, shade, showing the back."""

    sw: int
    off: int
    cover: Image.Image
    shade: Image.Image
    back: bool


class _Strip(NamedTuple):
    """One flip step placed on a tile: strip size and box, with the masks as ImagingCore objects
    (`Image.im`: private Pillow API, verified on 12.3.0; covered by tests)."""

    size: tuple[int, int]
    box: _Box
    cover: Any
    shade: Any
    back: bool


class _PlaneWork(NamedTuple):
    """One tile on one plane, as ImagingCore objects (see `_paint_step`; `Image.im`:
    private Pillow API, verified on 12.3.0; covered by tests)."""

    core: Any
    box: _Box
    face_front: Any
    face_back: Any
    shadow: Any
    strips: dict[int, _Strip | None]
    solid: int
    src_box: tuple[float, float, float, float]


_Geometry = dict[int, _Geo | None]


def to_planes(rgb: Image.Image) -> _Planes:
    """RGB -> (Y, Cb, Cr) L images: yuv420p, limited range."""
    y, cb, cr = rgb.convert("YCbCr").split()
    half = (rgb.width // 2, rgb.height // 2)
    return (
        y.point(_Y_LUT),
        cb.resize(half, Image.Resampling.BOX).point(_C_LUT),
        cr.resize(half, Image.Resampling.BOX).point(_C_LUT),
    )


def _geometry(tw: int, th: int, flip_n: int) -> _Geometry:
    """Per step k (1..flip_n-1): None if the strip is edge-on, else its `_Geo` for a tw x th
    tile. cover = antialiased trapezoid, shade = cover * (1 - brightness)."""
    steps: _Geometry = {}
    for k in range(1, flip_n):
        angle = math.pi * ease_in_out(k / flip_n)
        c = abs(math.cos(angle))
        sw = round(tw * c)
        if sw < 2:
            steps[k] = None
            continue
        inset = PERSPECTIVE * th * math.sin(angle) / 2
        ql, qr = (inset, 0.0) if angle < math.pi / 2 else (0.0, inset)
        big = Image.new("L", (sw * _SS, th * _SS), 0)
        ImageDraw.Draw(big).polygon(
            [
                (0, ql * _SS),
                (0, (th - ql) * _SS),
                (sw * _SS, (th - qr) * _SS),
                (sw * _SS, qr * _SS),
            ],
            fill=255,
        )
        cover = big.resize((sw, th), Image.Resampling.BOX)
        dark = 1 - (0.55 + 0.45 * c)
        shade = cover.point(lambda v, d=dark: round(v * d))
        steps[k] = _Geo(sw, (tw - sw) // 2, cover, shade, angle >= math.pi / 2)
    return steps


def _strips(geo: _Geometry, box: _Box) -> dict[int, _Strip | None]:
    """Each step's geometry placed on the tile at `box`."""
    # g.cover.im / g.shade.im below: private Pillow API, verified on 12.3.0; covered by tests
    th = box[3] - box[1]
    strips: dict[int, _Strip | None] = {}
    for k, g in geo.items():
        if g is None:
            strips[k] = None
            continue
        x0 = box[0] + g.off
        strips[k] = _Strip(
            (g.sw, th), (x0, box[1], x0 + g.sw, box[3]), g.cover.im, g.shade.im, g.back
        )
    return strips


def _tile_work(
    cores: list[Any],
    faces: tuple[_Planes, _Planes, _Planes],
    boxes: list[_Box],
    geometries: list[_Geometry],
) -> list[_PlaneWork]:
    """One tile's per-plane work: its faces cropped once, ready for the hot loop."""
    # the .im of each cropped face below: private Pillow API, verified on 12.3.0; covered by tests
    front, back, shadow = faces
    work = []
    for p, box in enumerate(boxes):
        src_box = (0.0, 0.0, float(box[2] - box[0]), float(box[3] - box[1]))
        work.append(
            _PlaneWork(
                cores[p],
                box,
                front[p].crop(box).im,
                back[p].crop(box).im,
                shadow[p].crop(box).im,
                _strips(geometries[p], box),
                SOLID[p],
                src_box,
            )
        )
    return work


def _tiles(
    canvas: _Planes, faces: tuple[_Planes, _Planes, _Planes], flip_n: int, spread_n: float
) -> list[tuple[int, list[_PlaneWork]]]:
    """Every tile as (start frame, per-plane work), in row-major order."""
    w, h = canvas[0].size
    rows = max(1, round(h / (w / COLS)))
    xs = [c * w // COLS for c in range(COLS + 1)]
    ys = [r * h // rows for r in range(rows + 1)]
    cores = [c.im for c in canvas]  # .im: private Pillow API, verified on 12.3.0; covered by tests
    cache: dict[tuple[int, int], _Geometry] = {}
    tiles = []
    for r in range(rows):
        for c in range(COLS):
            start = round(spread_n * (r + c) / max(1, rows + COLS - 2))
            boxes: list[_Box] = []
            geometries: list[_Geometry] = []
            for div in (1, 2, 2):
                box = (xs[c] // div, ys[r] // div, xs[c + 1] // div, ys[r + 1] // div)
                size = (box[2] - box[0], box[3] - box[1])
                if size not in cache:
                    cache[size] = _geometry(*size, flip_n)
                boxes.append(box)
                geometries.append(cache[size])
            tiles.append((start, _tile_work(cores, faces, boxes, geometries)))
    return tiles


def _emit(cores: list[Any], sizes: list[tuple[int, int]]) -> _Frame:
    out = []
    for core, (pw, ph) in zip(cores, sizes, strict=True):
        # Pillow's encoder directly: the whole plane in one chunk, no tobytes() wrapper or join
        # (Image._getencoder: private Pillow API, verified on 12.3.0; covered by tests)
        enc = Image._getencoder("L", "raw", "L")
        enc.setimage(core, (0, 0, pw, ph))
        _, err, data = enc.encode(pw * ph + pw)
        if err != 1:
            raise RuntimeError(f"plane encode failed: {err}")
        out.append(data)
    return out[0], out[1], out[2]


def _paint_step(work: list[_PlaneWork], k: int, flip_n: int) -> None:
    """Draw flip step k of one tile on all three planes."""
    near, bil = int(Image.Resampling.NEAREST), int(Image.Resampling.BILINEAR)
    for tile in work:
        if k == flip_n:
            tile.core.paste(tile.face_back, tile.box)
            continue
        tile.core.paste(tile.shadow, tile.box)
        strip = tile.strips[k]
        if strip is None:
            continue
        # ImagingCore.resize/paste directly: Image.resize/paste spend ~3x longer in their Python
        # wrappers (load, isinstance, _ensure_mutable, _new) than in C for these tiny strips
        # (ImagingCore calls: private Pillow API, verified on 12.3.0; covered by tests)
        face, mode = (tile.face_back, bil) if strip.back else (tile.face_front, near)
        tile.core.paste(face.resize(strip.size, mode, tile.src_box), strip.box, strip.cover)
        tile.core.paste(tile.solid, strip.box, strip.shade)


def render_yuv(front: Image.Image, original: Image.Image) -> Iterator[_Frame]:
    """yuv420p frames as (Y, Cb, Cr) bytes: `front` first, then the flip wave, ending on
    `original`."""
    if front.size != original.size:
        front = front.resize(original.size, Image.Resampling.NEAREST)
    flip_n = max(2, round(encode.FPS * FLIP_S))
    spread_n = encode.FPS * SPREAD_S

    shadow = ImageEnhance.Brightness(front).enhance(SHADOW_BRIGHTNESS)
    front_p, clear_p, shadow_p = to_planes(front), to_planes(original), to_planes(shadow)
    canvas = (front_p[0].copy(), front_p[1].copy(), front_p[2].copy())
    tiles = _tiles(canvas, (front_p, clear_p, shadow_p), flip_n, spread_n)

    last = max(s for s, _ in tiles) + flip_n
    schedule: list[list[tuple[int, int]]] = [[] for _ in range(last + 1)]
    for i, (start, _) in enumerate(tiles):
        for k in range(1, flip_n + 1):
            schedule[start + k].append((i, k))

    cores = [c.im for c in canvas]  # .im: private Pillow API, verified on 12.3.0; covered by tests
    sizes = [c.size for c in canvas]
    yield _emit(cores, sizes)
    for entries in schedule[1:]:
        for i, k in entries:
            _paint_step(tiles[i][1], k, flip_n)
        yield _emit(cores, sizes)


def render_part1(original: Image.Image, stages: list[Image.Image]) -> bytes:
    """Part 1 MP4: front stage -> diagonal wave of card flips -> the clear image, ending on it."""

    def planes() -> Iterator[bytes]:
        for frame in render_yuv(stages[0], original):
            yield from frame

    options = encode.Part1Options(pix_fmt="yuv420p", crf=CRF)
    return encode.encode_part1(planes(), original.size, options)
