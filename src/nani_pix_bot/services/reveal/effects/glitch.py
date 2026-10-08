"""Glitch reveal, part 1: the coarsest stage tears and splits while stepping through the real
stages, calming down as it goes, then a final burst and the clean clear frame. Glitch frames are
drawn on twos (15 fps; ffmpeg repeats each to the 30 fps output).

Every glitch op is a byte-slice on planar RGB rows, so no Pillow image is built per frame:
  - RGB split  = rotate the whole R / B plane buffer by +-shift bytes (pixels leaving a row's edge
                 re-enter the neighbouring row: invisible inside a 28px glitch fringe)
  - torn slice = rotate the slice's byte range in each plane
  - block      = row-wise slice assignment from the (unshifted) base planes
  - scanlines  = pre-darkened rows, built once per (stage, level) with bytes.translate
  - crush      = bytes.translate with a posterize LUT
Frames go to ffmpeg as gbrp (planar G, B, R); swscale converts to yuv420p in C.
"""

import math
import random
from collections.abc import Iterator

from PIL import Image

from nani_pix_bot.services.reveal import encode

CRF = 26
GLITCH_FPS = encode.FPS // 2  # glitch frames are drawn on twos
INTRO_S = 0.3
LEVELS = 8
SCAN_PERIOD = 3  # every Nth row darkened (approved look: 3)
_CRUSH = bytes(v & 0xE0 for v in range(256))  # == ImageOps.posterize(im, 3)

_Step = tuple[int, float, int]  # (stage, amount, seed)
_Planes = tuple[bytes, bytes, bytes]


def _darken_lut(level: int) -> bytes:
    f = (255 - round(60 * level / (LEVELS - 1))) / 255
    return bytes(round(v * f) for v in range(256))


class Base:
    """One image as three planes (r, g, b), plus lazily built scanlined variants per darkness
    level."""

    def __init__(self, img: Image.Image) -> None:
        self.w, self.h = img.size
        r, g, b = (p.tobytes() for p in img.split())
        self.planes: _Planes = (r, g, b)
        self._levels: dict[int, _Planes] = {0: self.planes}

    def at(self, level: int) -> _Planes:
        if level not in self._levels:
            lut = _darken_lut(level)
            r, g, b = (self._scanlined(p, lut) for p in self.planes)
            self._levels[level] = (r, g, b)
        return self._levels[level]

    def _scanlined(self, plane: bytes, lut: bytes) -> bytes:
        w, h = self.w, self.h
        mv = memoryview(plane)
        rows = range(0, h, SCAN_PERIOD)
        # darken only every 3rd row: gather, one translate, scatter back
        dark = b"".join(mv[y * w : (y + 1) * w] for y in rows).translate(lut)
        out = bytearray(plane)
        for i, y in enumerate(rows):
            out[y * w : (y + 1) * w] = dark[i * w : (i + 1) * w]
        return bytes(out)

    def gbrp(self) -> bytes:
        r, g, b = self.planes
        return g + b + r


def _split_channels(out: bytearray, planes: _Planes, n: int, s: int) -> None:
    """B moves right, R moves left (whole-plane rotation, 2 slice copies each); G stays."""
    sr, sg, sb = (memoryview(p) for p in planes)
    out[0:n] = sg
    out[n : n + s] = sb[n - s :]
    out[n + s : 2 * n] = sb[: n - s]
    out[2 * n : 3 * n - s] = sr[s:]
    out[3 * n - s : 3 * n] = sr[:s]


def _tear_slices(out: bytearray, rng: random.Random, size: tuple[int, int], amount: float) -> None:
    """Torn full-width slices: each rotated sideways in all three planes."""
    w, h = size
    n = w * h
    for _ in range(int(amount * 14)):
        y = rng.randrange(h)
        band = rng.randint(3, max(4, round(h * 0.08 * amount)))
        dx = round(rng.uniform(-1, 1) * w * 0.12 * amount)
        a, e = y * w, min(h, y + band) * w
        k = dx % (e - a)
        if k:
            for o in (0, n, 2 * n):
                seg = bytes(out[o + a : o + e])
                out[o + a : o + a + k] = seg[-k:]
                out[o + a + k : o + e] = seg[:-k]


def _displace_blocks(
    out: bytearray, rng: random.Random, size: tuple[int, int], planes: _Planes, amount: float
) -> None:
    """Displaced blocks, copied from the unshifted base planes (given in r, g, b order)."""
    w, h = size
    n = w * h
    sr, sg, sb = (memoryview(p) for p in planes)
    for _ in range(int(amount * 4)):
        bw, bh = rng.randint(w // 16, w // 5), rng.randint(h // 30, h // 10)
        x, y = rng.randrange(w - bw), rng.randrange(h - bh)
        dx, dy = round(rng.uniform(-1, 1) * w * 0.06), round(rng.uniform(-1, 1) * h * 0.03)
        tx, ty = min(w - bw, max(0, x + dx)), min(h - bh, max(0, y + dy))
        for o, q in zip((0, n, 2 * n), (sg, sb, sr), strict=True):
            for i in range(bh):
                si, d = (y + i) * w + x, o + (ty + i) * w + tx
                out[d : d + bw] = q[si : si + bw]


def glitch_frame(base: Base, amount: float, seed: int) -> bytes:
    """One glitched frame as gbrp bytes, written into a single output buffer (one copy per plane
    for the split, in-place for slices/blocks)."""
    rng = random.Random(seed)  # noqa: S311 - visual noise, seeded for a reproducible look
    if amount <= 0.02:
        return base.gbrp()
    level = min(LEVELS - 1, round(amount * (LEVELS - 1))) if amount > 0.3 else 0
    planes = base.at(level)
    size = (base.w, base.h)
    n = base.w * base.h
    out = bytearray(3 * n)  # gbrp: G at 0, B at n, R at 2n
    _split_channels(out, planes, n, round(amount * 28))
    _tear_slices(out, rng, size, amount)
    _displace_blocks(out, rng, size, planes, amount)
    if amount > 0.75 and rng.random() < 0.5:  # rare colour-crush burst on spikes
        return bytes(out.translate(_CRUSH))
    return bytes(out)


def plan(n_stages: int, rate: int, intro: float, seed: int = 3) -> list[_Step]:
    """(stage, amount, seed) per unique glitch frame at `rate` fps; stage n_stages = clear image."""
    rng = random.Random(seed)  # noqa: S311 - visual noise, seeded for a reproducible look
    steps = [
        (0, 0.25 + 0.25 * (i % 7 == 0), rng.randrange(1 << 30)) for i in range(round(rate * intro))
    ]
    n = int(rate * 2.4)
    for i in range(n):
        p = i / (n - 1)
        stage = min(n_stages - 1, math.floor(p * n_stages))
        amount = 0.85 * (1 - p) ** 0.8 + (0.6 if rng.random() < 0.08 else 0)
        steps.append((stage, min(1.0, amount), rng.randrange(1 << 30)))
    steps += [(n_stages, a, rng.randrange(1 << 30)) for a in (1.0, 0.8, 0.5, 0.2)]
    return steps


def frames(original: Image.Image, stages: list[Image.Image]) -> Iterator[bytes]:
    """gbrp frames at GLITCH_FPS: the clean coarsest stage first (ffmpeg holds it), then the
    glitched ones, the clean clear image last."""
    bases = [Base(s) for s in stages] + [Base(original)]
    yield bases[0].gbrp()
    for stage, amount, seed in plan(len(stages), GLITCH_FPS, INTRO_S):
        yield glitch_frame(bases[stage], amount, seed)
    yield bases[-1].gbrp()  # first fully-clear frame: part 1 ends here


def render_part1(original: Image.Image, stages: list[Image.Image]) -> bytes:
    """Part 1 MP4: glitching stages stepping to the clear image, ending on the clear frame."""
    k = encode.FPS // GLITCH_FPS
    hold_in = math.ceil(encode.HOLD_START * GLITCH_FPS)
    n_in = len(plan(len(stages), GLITCH_FPS, INTRO_S)) + 2  # + clean coarse first + clear last
    options = encode.Part1Options(
        pix_fmt="gbrp",
        input_fps=GLITCH_FPS,
        vf=f"tpad=start_mode=clone:start={hold_in}",
        # every input frame becomes k output frames; cap so the clear frame appears exactly once
        max_frames=k * (hold_in + n_in) - (k - 1),
        crf=CRF,
    )
    return encode.encode_part1(frames(original, stages), original.size, options)
