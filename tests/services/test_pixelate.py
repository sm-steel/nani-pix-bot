import io

import pytest
from PIL import Image

from nani_pix_bot.services.pixelate import pixelate

_TEST_IMAGE_SIZE = 192


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


def test_pixelate_preserves_original_dimensions() -> None:
    original = _gradient_png()

    result = pixelate(original, 12)

    with Image.open(io.BytesIO(result)) as image:
        assert image.size == (_TEST_IMAGE_SIZE, _TEST_IMAGE_SIZE)


@pytest.mark.parametrize("target_width", [12, 25, 64, 128, 512])
def test_pixelate_blocks_match_the_target_width(target_width: int) -> None:
    # Sized as an exact multiple of the target width so block boundaries
    # divide evenly, whatever width is passed in.
    block_size = 8
    original = _gradient_png(target_width * block_size)

    result = pixelate(original, target_width)

    with Image.open(io.BytesIO(result)) as image:
        rgb = image.convert("RGB")
        pixels = rgb.load()
        assert pixels is not None
        top_left_block = {pixels[x, y] for x in range(block_size) for y in range(block_size)}
        assert len(top_left_block) == 1


def test_pixelate_smaller_width_is_blockier() -> None:
    original = _gradient_png()

    blockiest = pixelate(original, 12)
    clearest = pixelate(original, 128)

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


def test_pixelate_is_resolution_independent() -> None:
    """The whole point of downscaling to a fixed target width rather than
    a divisor of the source resolution: the same width should pixelate to
    the same number of blocks across the image regardless of the source
    image's resolution."""
    small = _gradient_png(192)
    large = _gradient_png(768)

    small_result = pixelate(small, 12)
    large_result = pixelate(large, 12)

    small_transitions = _count_color_transitions_in_row(small_result, y=0)
    large_transitions = _count_color_transitions_in_row(large_result, y=0)

    expected = 12 - 1
    assert small_transitions == expected
    assert large_transitions == expected
