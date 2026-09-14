"""TEMPORARY exploration code — DELETE ME together with testpixels.py,
its registration in app.py, and the tmp/example*.* images, once the
obfuscation-algorithm question is answered.

A registry of candidate mosaic algorithms for /testpixels to post side
by side. Deliberately NOT in services/ and deliberately not
test-covered: services/ is the production layer this repo holds to TDD,
and everything here is throwaway probe code with a known short lifespan.
The live pipeline (services/pixelate.py) is imported unchanged as the
`nearest` baseline and is not modified by this exploration.

Every algorithm here is a mosaic taking a **target width**, the same
vocabulary the live stage config uses, so difficulty stays independent
of the source screenshot's resolution (see services/pixelate.py's
docstring). They differ only in how each block's single colour is
chosen: an arbitrary sample, a mean, a median, a mode, or a windowed
resample.

Blur was explored here and removed entirely. Whole-image blurs
(Gaussian, box, and a BICUBIC upscale that reached the same place) fail
on resolution independence — a radius that obscures a 1280px screenshot
leaves a 3840px one readable — and at any radius strong enough to matter
the result stops being a mosaic. Softening a mosaic with a blur sized to
a fraction of a block fixed the scaling problem but was not wanted
either: it trades away the crisp block edges that make the reveal feel
stepped rather than smeared. What is left is mosaics alone.
"""

import io
from collections.abc import Callable
from dataclasses import dataclass
from functools import partial

from loguru import logger
from PIL import Image, ImageFilter

from nani_pix_bot.services.pixelate import pixelate

# Rank filters (median/mode) are O(size^2) per pixel, so running one at
# full resolution with a block-sized window is unusably slow. Instead
# the image is first box-downscaled to this multiple of the target
# width, where a fixed 3x3 window covers exactly one block.
_OVERSAMPLE = 3


def _scaled(size: tuple[int, int], width: int) -> tuple[int, int]:
    """`size` scaled to `width`, preserving aspect ratio."""
    source_width, source_height = size
    return (width, max(1, round(source_height * width / source_width)))


def _apply(image_bytes: bytes, transform: Callable[[Image.Image], Image.Image]) -> bytes:
    with Image.open(io.BytesIO(image_bytes)) as source:
        rgb = source.convert("RGB")
    result = transform(rgb)
    buffer = io.BytesIO()
    result.save(buffer, format="PNG")
    return buffer.getvalue()


def _mosaic(image_bytes: bytes, width: int, *, down: Image.Resampling) -> bytes:
    """Downscale to `width` with `down`, back up to full size with NEAREST."""

    def run(rgb: Image.Image) -> Image.Image:
        full = rgb.size
        return rgb.resize(_scaled(full, width), down).resize(full, Image.Resampling.NEAREST)

    return _apply(image_bytes, run)


def _rank_mosaic(image_bytes: bytes, width: int, *, kernel: ImageFilter.Filter) -> bytes:
    """Mosaic whose block colour is a rank statistic (median/mode) of the
    block rather than a mean or an arbitrary sample."""

    def run(rgb: Image.Image) -> Image.Image:
        full = rgb.size
        oversampled = min(width * _OVERSAMPLE, full[0])
        small = rgb.resize(_scaled(full, oversampled), Image.Resampling.BOX)
        blocks = small.filter(kernel).resize(_scaled(full, width), Image.Resampling.NEAREST)
        return blocks.resize(full, Image.Resampling.NEAREST)

    return _apply(image_bytes, run)


@dataclass(frozen=True)
class Algo:
    help: str
    render: Callable[[bytes, int], bytes]


ALGORITHMS: dict[str, Algo] = {
    "nearest": Algo(
        "LIVE algorithm: NEAREST down (one arbitrary pixel per block)",
        pixelate,
    ),
    "box": Algo(
        "canonical mosaic: each block is the average colour of its pixels",
        partial(_mosaic, down=Image.Resampling.BOX),
    ),
    "median": Algo(
        "block colour is the median — keeps the dominant tone, no grey mush",
        partial(_rank_mosaic, kernel=ImageFilter.MedianFilter(_OVERSAMPLE)),
    ),
    "mode": Algo(
        "block colour is the most frequent one — speckles badly on real screenshots",
        partial(_rank_mosaic, kernel=ImageFilter.ModeFilter(_OVERSAMPLE)),
    ),
    "lanczos": Algo(
        "LANCZOS down, NEAREST up — sharpest block colours, may ring on edges",
        partial(_mosaic, down=Image.Resampling.LANCZOS),
    ),
}


def render(algo_name: str, image_bytes: bytes, width: int) -> bytes:
    result = ALGORITHMS[algo_name].render(image_bytes, width)
    logger.debug("testpixels: rendered {} at width {} -> {} bytes", algo_name, width, len(result))
    return result
