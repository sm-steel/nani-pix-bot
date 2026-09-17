import io

import pytest
from PIL import Image

from nani_pix_bot.models.enums import (
    DEFAULT_ALGORITHM,
    DISCOURAGED_ALGORITHMS,
    PixelAlgorithm,
)
from nani_pix_bot.services.pixelate import ALGORITHMS, pixelate

_TEST_IMAGE_SIZE = 192
# Every algorithm must satisfy the shared mosaic properties below; which
# one is under test only matters for the identity and default cases.
_EVERY_ALGORITHM = list(PixelAlgorithm)


def _gradient_png(size: int = _TEST_IMAGE_SIZE) -> bytes:
    """A high-frequency image (every pixel a distinct color) so pixelation's
    blockiness is easy to detect — a smooth/solid image wouldn't show it."""
    image = Image.new("RGB", (size, size))
    pixels = image.load()
    assert pixels is not None
    for x in range(size):
        for y in range(size):
            pixels[x, y] = (x % 256, y % 256, (x + y) % 256)
    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    return buffer.getvalue()


def _unique_colors(png_bytes: bytes) -> int:
    with Image.open(io.BytesIO(png_bytes)) as image:
        return len(set(image.convert("RGB").get_flattened_data()))


def test_the_default_algorithm_is_median() -> None:
    """The /testpixels spike's verdict: NEAREST samples one arbitrary
    pixel per block and visibly speckles on real screenshots, where a
    rank-based mosaic keeps the block's dominant tone."""
    assert DEFAULT_ALGORITHM is PixelAlgorithm.MEDIAN


def test_every_algorithm_has_an_implementation() -> None:
    """The registry is what the starter's picker enumerates, so a member
    added to the enum without an implementation must fail here rather
    than at render time in a live game."""
    assert set(ALGORITHMS) == set(PixelAlgorithm)


def test_mode_is_the_only_discouraged_algorithm() -> None:
    """Marked worst in the picker: ModeFilter falls back to the centre
    pixel whenever no colour repeats in its window, which on a real
    (gradient-heavy, compression-softened) screenshot is most windows —
    so it speckles harder than the NEAREST it was meant to improve on."""
    assert PixelAlgorithm.MODE in DISCOURAGED_ALGORITHMS
    assert len(DISCOURAGED_ALGORITHMS) == 1


def _legacy_pixelate(image_bytes: bytes, target_width: int) -> bytes:
    """The pre-package implementation, verbatim, as a reference."""
    with Image.open(io.BytesIO(image_bytes)) as source:
        rgb = source.convert("RGB")
    width, height = rgb.size
    scale = target_width / width
    small_size = (target_width, max(1, round(height * scale)))
    small = rgb.resize(small_size, Image.Resampling.NEAREST)
    pixelated = small.resize((width, height), Image.Resampling.NEAREST)
    buffer = io.BytesIO()
    pixelated.save(buffer, format="PNG")
    return buffer.getvalue()


@pytest.mark.parametrize("target_width", [12, 64, 128])
def test_nearest_still_matches_the_pre_package_output(target_width: int) -> None:
    """NEAREST is what every existing game was rendered with, so the
    refactor must not shift it by even a byte."""
    original = _gradient_png()

    assert pixelate(original, target_width, PixelAlgorithm.NEAREST) == _legacy_pixelate(
        original, target_width
    )


@pytest.mark.parametrize("algorithm", _EVERY_ALGORITHM)
def test_pixelate_preserves_original_dimensions(algorithm: PixelAlgorithm) -> None:
    original = _gradient_png()

    result = pixelate(original, 12, algorithm)

    with Image.open(io.BytesIO(result)) as image:
        assert image.size == (_TEST_IMAGE_SIZE, _TEST_IMAGE_SIZE)


@pytest.mark.parametrize("algorithm", _EVERY_ALGORITHM)
@pytest.mark.parametrize("target_width", [12, 25, 64, 128, 512])
def test_pixelate_blocks_match_the_target_width(
    target_width: int, algorithm: PixelAlgorithm
) -> None:
    # Sized as an exact multiple of the target width so block boundaries
    # divide evenly, whatever width is passed in.
    block_size = 8
    original = _gradient_png(target_width * block_size)

    result = pixelate(original, target_width, algorithm)

    with Image.open(io.BytesIO(result)) as image:
        rgb = image.convert("RGB")
        pixels = rgb.load()
        assert pixels is not None
        top_left_block = {pixels[x, y] for x in range(block_size) for y in range(block_size)}
        assert len(top_left_block) == 1


@pytest.mark.parametrize("algorithm", _EVERY_ALGORITHM)
def test_pixelate_smaller_width_is_blockier(algorithm: PixelAlgorithm) -> None:
    original = _gradient_png()

    blockiest = pixelate(original, 12, algorithm)
    clearest = pixelate(original, 128, algorithm)

    assert _unique_colors(blockiest) < _unique_colors(clearest)


def _count_color_transitions_in_row(png_bytes: bytes, *, y: int) -> int:
    with Image.open(io.BytesIO(png_bytes)) as image:
        rgb = image.convert("RGB")
        pixels = rgb.load()
        assert pixels is not None
        width = rgb.size[0]
        previous = pixels[0, y]
        transitions = 0
        for x in range(1, width):
            current = pixels[x, y]
            if current != previous:
                transitions += 1
            previous = current
        return transitions


@pytest.mark.parametrize("algorithm", _EVERY_ALGORITHM)
def test_pixelate_is_resolution_independent(algorithm: PixelAlgorithm) -> None:
    """The whole point of downscaling to a fixed target width rather than
    a divisor of the source resolution: the same width should pixelate to
    the same number of blocks across the image regardless of the source
    image's resolution."""
    small = _gradient_png(192)
    large = _gradient_png(768)

    small_result = pixelate(small, 12, algorithm)
    large_result = pixelate(large, 12, algorithm)

    small_transitions = _count_color_transitions_in_row(small_result, y=0)
    large_transitions = _count_color_transitions_in_row(large_result, y=0)

    expected = 12 - 1
    assert small_transitions == expected
    assert large_transitions == expected
