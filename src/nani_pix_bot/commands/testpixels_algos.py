"""TEMPORARY exploration code — DELETE ME together with testpixels.py,
its registration in app.py, and the tmp/example*.* images, once the
obfuscation-algorithm question is answered.

A registry of candidate image-obfuscation algorithms for /testpixels to
post side by side. Deliberately NOT in services/ and deliberately not
test-covered: services/ is the production layer this repo holds to TDD,
and everything here is throwaway probe code with a known short lifespan.
The live pipeline (services/pixelate.py) is imported unchanged as the
`nearest` baseline and is not modified by this exploration.

Two families, with deliberately different parameters:

* mosaic/hybrid algorithms take a **target width**, the same vocabulary
  the live stage config uses, so their difficulty is independent of the
  source screenshot's resolution (see services/pixelate.py's docstring);
* blur algorithms take a **raw pixel radius**, which has no such
  resolution independence — the same radius obscures a 1280px
  screenshot far more than a 3840px one. That asymmetry is a real
  property of blur worth seeing, not something to paper over, so the
  radius is passed through as typed rather than rescaled per image.
"""

import io
from collections.abc import Callable
from dataclasses import dataclass
from typing import Literal

from loguru import logger
from PIL import Image, ImageFilter

from nani_pix_bot.services.pixelate import pixelate

# Nominal source width used to turn a stage's target width into a
# comparable default blur radius: a target width of W keeps roughly
# W blocks across the image, so a block is _REFERENCE_WIDTH / W pixels
# and a blur of half a block is about as destructive.
_REFERENCE_WIDTH = 1920

# Rank filters (median/mode) are O(size^2) per pixel, so running one at
# full resolution with a block-sized window is unusably slow. Instead
# the image is first box-downscaled to this multiple of the target
# width, where a fixed 3x3 window covers exactly one block.
_OVERSAMPLE = 3

ParamKind = Literal["width", "radius"]


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


def _mosaic(
    image_bytes: bytes,
    width: int,
    *,
    down: Image.Resampling,
    up: Image.Resampling = Image.Resampling.NEAREST,
) -> bytes:
    """Downscale to `width` with `down`, back up to full size with `up`."""

    def run(rgb: Image.Image) -> Image.Image:
        return rgb.resize(_scaled(rgb.size, width), down).resize(rgb.size, up)

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


def _mosaic_blur(image_bytes: bytes, width: int) -> bytes:
    """Box mosaic softened by a blur of about half a block — blobs with
    no grid edges for players to read silhouettes off."""

    def run(rgb: Image.Image) -> Image.Image:
        full = rgb.size
        small = rgb.resize(_scaled(full, width), Image.Resampling.BOX)
        blocky = small.resize(full, Image.Resampling.NEAREST)
        return blocky.filter(ImageFilter.GaussianBlur(max(1.0, full[0] / width / 2)))

    return _apply(image_bytes, run)


def _gaussian(image_bytes: bytes, radius: int) -> bytes:
    return _apply(image_bytes, lambda rgb: rgb.filter(ImageFilter.GaussianBlur(radius)))


def _box_blur(image_bytes: bytes, radius: int) -> bytes:
    return _apply(image_bytes, lambda rgb: rgb.filter(ImageFilter.BoxBlur(radius)))


@dataclass(frozen=True)
class Algo:
    param_kind: ParamKind
    help: str
    render: Callable[[bytes, int], bytes]

    def default_param(self, stage_width: int) -> int:
        """This algorithm's own setting roughly equivalent to the live
        stage config's `stage_width`."""
        if self.param_kind == "width":
            return stage_width
        return max(1, round(_REFERENCE_WIDTH / stage_width / 2))


ALGORITHMS: dict[str, Algo] = {
    "nearest": Algo(
        "width",
        "LIVE algorithm: NEAREST down, NEAREST up (one arbitrary pixel per block)",
        pixelate,
    ),
    "box": Algo(
        "width",
        "canonical mosaic: each block is the average colour of its pixels",
        lambda data, param: _mosaic(data, param, down=Image.Resampling.BOX),
    ),
    "median": Algo(
        "width",
        "block colour is the median — keeps the dominant tone, no grey mush",
        lambda data, param: _rank_mosaic(data, param, kernel=ImageFilter.MedianFilter(_OVERSAMPLE)),
    ),
    "mode": Algo(
        "width",
        "block colour is the most frequent one — suits flat cel-shaded art",
        lambda data, param: _rank_mosaic(data, param, kernel=ImageFilter.ModeFilter(_OVERSAMPLE)),
    ),
    "lanczos": Algo(
        "width",
        "LANCZOS down, NEAREST up — sharpest block colours, may ring on edges",
        lambda data, param: _mosaic(data, param, down=Image.Resampling.LANCZOS),
    ),
    "smooth": Algo(
        "width",
        "resolution blur: BOX down, BICUBIC up — a low-pass with no block grid",
        lambda data, param: _mosaic(
            data,
            param,
            down=Image.Resampling.BOX,
            up=Image.Resampling.BICUBIC,
        ),
    ),
    "mosaicblur": Algo(
        "width",
        "box mosaic softened by half a block of Gaussian — soft blobs",
        _mosaic_blur,
    ),
    "gaussian": Algo(
        "radius",
        "plain Gaussian blur, the standard obfuscation blur",
        _gaussian,
    ),
    "boxblur": Algo(
        "radius",
        "plain box blur — cheaper, boxier falloff than Gaussian",
        _box_blur,
    ),
}


def render(algo_name: str, image_bytes: bytes, param: int) -> bytes:
    algo = ALGORITHMS[algo_name]
    result = algo.render(image_bytes, param)
    logger.debug(
        "testpixels: rendered {} at {}={} -> {} bytes",
        algo_name,
        algo.param_kind,
        param,
        len(result),
    )
    return result
