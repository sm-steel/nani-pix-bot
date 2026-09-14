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
docstring). Whole-image blurs were tried and removed: a raw pixel radius
obscures a 1280px screenshot far more than a 3840px one, and at any
radius strong enough to matter the result stopped being a mosaic at all.

What survives of that idea is *softening* — a blur small enough to round
off the block edges without dissolving the grid, applied on top of a
mosaic. Its radius is a fraction of one block, so it scales with the
target width like everything else here. The fractions in _SOFT_LEVELS
bracket the useful range: at block/12 the grid is untouched but for its
corners, and by block/4 the blocks have melted into blobs. Each softened
variant is registered separately from its unsoftened base so the two can
be compared directly in one album.
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

# Softening blur radii, as divisors of one block. Subtle to soft; going
# past block/6 stops reading as a mosaic (block/2 was the original
# mosaicblur, and it looked like a plain blur).
_SOFT_LEVELS = (12, 8, 6)

# The mosaics that get softened variants: the live algorithm, to test
# "keep today's pipeline and just soften it", and the cleanest mosaic,
# to see softening in isolation from sampling noise.
_SOFT_BASES = (
    ("nearest", Image.Resampling.NEAREST),
    ("box", Image.Resampling.BOX),
)


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
    soften: int | None = None,
) -> bytes:
    """Downscale to `width` with `down` and back up with NEAREST. When
    `soften` is set, finish with a Gaussian of one block over `soften`."""

    def run(rgb: Image.Image) -> Image.Image:
        full = rgb.size
        blocky = rgb.resize(_scaled(full, width), down).resize(full, Image.Resampling.NEAREST)
        if soften is None:
            return blocky
        return blocky.filter(ImageFilter.GaussianBlur(full[0] / width / soften))

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


def _build_registry() -> dict[str, Algo]:
    """Ordered so each softenable base is immediately followed by its own
    softened variants — an album then reads as "plain, then gently
    softened, then more so" for that base before moving to the next."""
    registry: dict[str, Algo] = {}
    bases = {
        "nearest": Algo("LIVE algorithm: NEAREST down (one arbitrary pixel per block)", pixelate),
        "box": Algo(
            "canonical mosaic: each block is the average colour of its pixels",
            partial(_mosaic, down=Image.Resampling.BOX),
        ),
    }
    for name, resampling in _SOFT_BASES:
        registry[name] = bases[name]
        for level in _SOFT_LEVELS:
            registry[f"{name}-soft{level}"] = Algo(
                f"{name} mosaic softened by a Gaussian of one block / {level}",
                partial(_mosaic, down=resampling, soften=level),
            )
    registry["median"] = Algo(
        "block colour is the median — keeps the dominant tone, no grey mush",
        partial(_rank_mosaic, kernel=ImageFilter.MedianFilter(_OVERSAMPLE)),
    )
    registry["mode"] = Algo(
        "block colour is the most frequent one — speckles badly on real screenshots",
        partial(_rank_mosaic, kernel=ImageFilter.ModeFilter(_OVERSAMPLE)),
    )
    registry["lanczos"] = Algo(
        "LANCZOS down, NEAREST up — sharpest block colours, may ring on edges",
        partial(_mosaic, down=Image.Resampling.LANCZOS),
    )
    return registry


ALGORITHMS: dict[str, Algo] = _build_registry()


def render(algo_name: str, image_bytes: bytes, width: int) -> bytes:
    result = ALGORITHMS[algo_name].render(image_bytes, width)
    logger.debug("testpixels: rendered {} at width {} -> {} bytes", algo_name, width, len(result))
    return result
