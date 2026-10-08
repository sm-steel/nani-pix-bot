"""Maps each `PixelAlgorithm` to its implementation, and applies one.

The registry is deliberately **data** over two shared primitives rather
than five near-identical functions — both because that is the honest
shape (the algorithms differ only in which filter colours a block) and
because five copies of the same four lines is exactly what qlty's
duplication check exists to catch.

Takes a raw width rather than a `PixelStage`: which width applies to
which stage is admin-configurable and DB-backed (see
services/settings/stage_config.py), so callers resolve that themselves
and this package stays a pure, DB-agnostic image-processing unit.
"""

from collections.abc import Callable
from functools import partial

from loguru import logger
from PIL import Image, ImageFilter

from nani_pix_bot.models.enums import PixelAlgorithm
from nani_pix_bot.services.pixelate.render import (
    OVERSAMPLE,
    apply,
    mosaic_image,
    rank_mosaic_image,
)

IMAGE_ALGORITHMS: dict[PixelAlgorithm, Callable[..., Image.Image]] = {
    PixelAlgorithm.NEAREST: partial(mosaic_image, down=Image.Resampling.NEAREST),
    PixelAlgorithm.BOX: partial(mosaic_image, down=Image.Resampling.BOX),
    PixelAlgorithm.LANCZOS: partial(mosaic_image, down=Image.Resampling.LANCZOS),
    PixelAlgorithm.MEDIAN: partial(rank_mosaic_image, kernel=ImageFilter.MedianFilter(OVERSAMPLE)),
    PixelAlgorithm.MODE: partial(rank_mosaic_image, kernel=ImageFilter.ModeFilter(OVERSAMPLE)),
}


def apply_algorithm(transform: Callable[..., Image.Image], image_bytes: bytes, width: int) -> bytes:
    """Bytes-in, PNG-bytes-out wrapper around one `IMAGE_ALGORITHMS` entry."""
    return apply(image_bytes, partial(transform, width=width))


ALGORITHMS: dict[PixelAlgorithm, Callable[[bytes, int], bytes]] = {
    algorithm: partial(apply_algorithm, transform)
    for algorithm, transform in IMAGE_ALGORITHMS.items()
}


def pixelate_image(image: Image.Image, target_width: int, algorithm: PixelAlgorithm) -> Image.Image:
    """`pixelate` for an already-decoded RGB image (the reveal worker renders
    five stages from one decode). Same pixels as `pixelate`, no PNG round-trip."""
    return IMAGE_ALGORITHMS[algorithm](image.convert("RGB"), target_width)


def pixelate(image_bytes: bytes, target_width: int, algorithm: PixelAlgorithm) -> bytes:
    """Pixelate `image_bytes` to `target_width` using `algorithm`.

    `algorithm` is required rather than defaulted on purpose: every
    caller rendering a *game's* image must pass that game's own choice,
    and a default here would let a missed call site quietly render the
    wrong thing instead of failing the type check."""
    result = ALGORITHMS[algorithm](image_bytes, target_width)
    logger.debug(
        "pixelated image to target width {target_width} with {algorithm} -> {size} bytes",
        target_width=target_width,
        algorithm=algorithm.value,
        size=len(result),
    )
    return result
