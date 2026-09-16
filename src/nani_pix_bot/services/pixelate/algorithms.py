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
from nani_pix_bot.services.pixelate.render import OVERSAMPLE, mosaic, rank_mosaic

ALGORITHMS: dict[PixelAlgorithm, Callable[[bytes, int], bytes]] = {
    PixelAlgorithm.NEAREST: partial(mosaic, down=Image.Resampling.NEAREST),
    PixelAlgorithm.BOX: partial(mosaic, down=Image.Resampling.BOX),
    PixelAlgorithm.LANCZOS: partial(mosaic, down=Image.Resampling.LANCZOS),
    PixelAlgorithm.MEDIAN: partial(rank_mosaic, kernel=ImageFilter.MedianFilter(OVERSAMPLE)),
    PixelAlgorithm.MODE: partial(rank_mosaic, kernel=ImageFilter.ModeFilter(OVERSAMPLE)),
}


def pixelate(image_bytes: bytes, target_width: int, algorithm: PixelAlgorithm) -> bytes:
    """Pixelate `image_bytes` to `target_width` using `algorithm`.

    `algorithm` is required rather than defaulted on purpose: every
    caller rendering a *game's* image must pass that game's own choice,
    and a default here would let a missed call site quietly render the
    wrong thing instead of failing the type check."""
    result = ALGORITHMS[algorithm](image_bytes, target_width)
    logger.debug(
        "Pixelated image to target width {} with {} -> {} bytes",
        target_width,
        algorithm.value,
        len(result),
    )
    return result
