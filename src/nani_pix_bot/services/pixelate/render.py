"""The Pillow primitives every pixelation algorithm is built from — the
decode/encode wrapper and the two mosaic shapes. Which block-colouring
strategy maps to which `PixelAlgorithm` is algorithms.py's job; this
module knows nothing about the enum.

Every algorithm downscales to a fixed *target width*, not a divisor of
the source resolution — a divisor barely pixelates a high-resolution
screenshot (÷10 of a 2560px-wide image is still 256px wide, plenty
detailed). A fixed target keeps difficulty independent of screenshot
resolution: a 12px-wide image reads as pure colour blobs whether the
original was 1280px or 3840px wide.

No image bytes are ever persisted; callers regenerate a stage's image
from the original screenshot's bytes on demand.
"""

import io
from collections.abc import Callable

from PIL import Image, ImageFilter

# Rank filters (median/mode) are O(size^2) per pixel, so running one at
# full resolution with a block-sized window is unusably slow — seconds
# per image on a 2560px screenshot. Box-downscaling to this multiple of
# the target width first makes a fixed 3x3 window cover exactly one
# block, which is the same statistic for a fraction of the work.
OVERSAMPLE = 3


def scaled(size: tuple[int, int], width: int) -> tuple[int, int]:
    """`size` scaled to `width`, preserving aspect ratio."""
    source_width, source_height = size
    return (width, max(1, round(source_height * width / source_width)))


def apply(image_bytes: bytes, transform: Callable[[Image.Image], Image.Image]) -> bytes:
    """Decode to RGB, hand the image to `transform`, re-encode as PNG."""
    with Image.open(io.BytesIO(image_bytes)) as source:
        rgb = source.convert("RGB")
    result = transform(rgb)
    buffer = io.BytesIO()
    result.save(buffer, format="PNG")
    return buffer.getvalue()


def mosaic(image_bytes: bytes, width: int, *, down: Image.Resampling) -> bytes:
    """Downscale to `width` with `down`, then back up to the original
    size with NEAREST — the blocky pixelation effect. The downscale
    filter is what picks each block's colour: NEAREST samples one
    arbitrary pixel, BOX averages, LANCZOS uses a windowed resample."""

    def run(rgb: Image.Image) -> Image.Image:
        full = rgb.size
        return rgb.resize(scaled(full, width), down).resize(full, Image.Resampling.NEAREST)

    return apply(image_bytes, run)


def rank_mosaic(image_bytes: bytes, width: int, *, kernel: ImageFilter.Filter) -> bytes:
    """A mosaic whose block colour is a rank statistic of the block (a
    median or a mode) rather than a mean or an arbitrary sample. See
    OVERSAMPLE for why this takes the scenic route rather than filtering
    at full resolution."""

    def run(rgb: Image.Image) -> Image.Image:
        full = rgb.size
        # Never upscale on the way in: a target width close to the
        # source's would otherwise enlarge before filtering.
        oversampled = min(width * OVERSAMPLE, full[0])
        small = rgb.resize(scaled(full, oversampled), Image.Resampling.BOX)
        blocks = small.filter(kernel).resize(scaled(full, width), Image.Resampling.NEAREST)
        return blocks.resize(full, Image.Resampling.NEAREST)

    return apply(image_bytes, run)
