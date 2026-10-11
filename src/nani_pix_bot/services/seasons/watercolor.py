"""The Sakura Season stage style: a watercolor wash over the pixelated stage
image (seasons spec §7). It only softens: a light bleed, wet pigment edges,
paper grain, uneven drying and a thinned painted border. Deterministic (fixed
seeds), same size, RGB."""

import random
from functools import lru_cache

from PIL import Image, ImageChops, ImageDraw, ImageFilter, ImageOps

PAPER = (247, 240, 226)
SEED = 1597
# Per-size layers kept: stage images come in a handful of widths at most.
LAYER_CACHE_SIZE = 4


@lru_cache(maxsize=LAYER_CACHE_SIZE)
def _grain(size: tuple[int, int]) -> Image.Image:
    """Paper grain: low-res seeded noise, blurred up — fibres and blotches."""
    rng = random.Random(SEED)  # noqa: S311 - seeded decoration, not security
    w, h = size
    small = Image.new("L", (max(1, w // 6), max(1, h // 6)))
    small.putdata([rng.randint(0, 255) for _ in range(small.width * small.height)])
    fine = small.resize(size, Image.Resampling.BICUBIC).filter(ImageFilter.GaussianBlur(1.2))
    blot = Image.new("L", (max(1, w // 60), max(1, h // 60)))
    blot.putdata([rng.randint(0, 255) for _ in range(blot.width * blot.height)])
    blot = blot.resize(size, Image.Resampling.BICUBIC).filter(ImageFilter.GaussianBlur(w / 80))
    return Image.blend(fine, blot, 0.5)


@lru_cache(maxsize=LAYER_CACHE_SIZE)
def _blots(size: tuple[int, int]) -> Image.Image:
    rng = random.Random(SEED + 1)  # noqa: S311 - seeded decoration, not security
    w, _ = size
    small = Image.new("L", (12, 7))
    small.putdata([rng.randint(0, 255) for _ in range(12 * 7)])
    big = small.resize(size, Image.Resampling.BICUBIC).filter(ImageFilter.GaussianBlur(w / 40))
    return big.point(lambda v: max(0, v - 150) * 2)


@lru_cache(maxsize=LAYER_CACHE_SIZE)
def _edge_mask(size: tuple[int, int]) -> Image.Image:
    """White inside, ragged soft falloff at the border."""
    rng = random.Random(SEED + 2)  # noqa: S311 - seeded decoration, not security
    w, h = size
    m = Image.new("L", size, 0)
    d = ImageDraw.Draw(m)
    inset = w * 0.022
    steps = 60
    pts = [(w * i / steps, inset + rng.uniform(-1, 1) * inset * 0.6) for i in range(steps)]  # top
    pts += [(w - inset + rng.uniform(-1, 1) * inset * 0.6, h * i / steps) for i in range(steps)]
    pts += [
        (w - w * i / steps, h - inset + rng.uniform(-1, 1) * inset * 0.6) for i in range(steps)
    ]  # bottom
    pts += [(inset + rng.uniform(-1, 1) * inset * 0.6, h - h * i / steps) for i in range(steps)]
    d.polygon(pts, fill=255)
    return m.filter(ImageFilter.GaussianBlur(inset * 0.5))


def watercolor(image: Image.Image) -> Image.Image:
    rgb = image.convert("RGB")
    w, _ = rgb.size
    paper = Image.new("RGB", rgb.size, PAPER)
    # 1. a light bleed: soften block seams a touch, keep detail
    wash = rgb.filter(ImageFilter.GaussianBlur(w / 900))
    # 2. pigment on warm paper: a little lighter, colours kept lively
    wash = Image.blend(wash, paper, 0.08)
    # 3. wet edges: pigment pools along colour changes as thin darker rims
    soft = wash.filter(ImageFilter.GaussianBlur(w / 300))
    rim = ImageOps.grayscale(ImageChops.difference(wash, soft))
    rim = rim.point(lambda v: min(255, v * 4)).filter(ImageFilter.GaussianBlur(w / 1200))
    darker = ImageChops.multiply(wash, Image.new("RGB", rgb.size, (200, 185, 190)))
    wash = Image.composite(darker, wash, rim.point(lambda v: v * 0.6))
    # 4. granulation: soft blotchy paper texture, gentle
    grain = _grain(rgb.size).point(lambda v: 238 + v * 17 // 255)
    wash = ImageChops.multiply(wash, Image.merge("RGB", (grain, grain, grain)))
    # 5. uneven pigment: some patches a shade lighter, like a wash drying
    lighter = Image.blend(wash, paper, 0.22)
    wash = Image.composite(lighter, wash, _blots(rgb.size))
    # 6. painted edges: the wash thins out into bare paper at the border
    return Image.composite(wash, paper, _edge_mask(rgb.size))
