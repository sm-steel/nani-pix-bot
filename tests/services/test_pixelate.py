import io

import pytest
from PIL import Image

from nani_pix_bot.models.enums import PixelStage
from nani_pix_bot.services.pixelate import STAGE_TARGET_WIDTH, pixelate

# A multiple of every target width (12, 24, 48, 64) so block sizes divide evenly.
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

    result = pixelate(original, PixelStage.STAGE_1)

    with Image.open(io.BytesIO(result)) as image:
        assert image.size == (_TEST_IMAGE_SIZE, _TEST_IMAGE_SIZE)


@pytest.mark.parametrize("stage", list(PixelStage))
def test_pixelate_blocks_match_the_stages_target_width(stage: PixelStage) -> None:
    # Sized as an exact multiple of the stage's own target width — the 5
    # stage widths (12/25/38/51/64) share no convenient common multiple
    # the way the old 4 (12/24/48/64) did, so each stage gets an image
    # sized just for it rather than one shared module-level constant.
    target_width = STAGE_TARGET_WIDTH[stage]
    block_size = 8
    original = _gradient_png(target_width * block_size)

    result = pixelate(original, stage)

    with Image.open(io.BytesIO(result)) as image:
        rgb = image.convert("RGB")
        pixels = rgb.load()
        assert pixels is not None
        top_left_block = {pixels[x, y] for x in range(block_size) for y in range(block_size)}
        assert len(top_left_block) == 1


def test_pixelate_first_stage_is_blockier_than_last() -> None:
    original = _gradient_png()

    stage_1 = pixelate(original, PixelStage.STAGE_1)
    stage_5 = pixelate(original, PixelStage.STAGE_5)

    assert _unique_colors(stage_1) < _unique_colors(stage_5)


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
    """The whole point of switching away from a divisor-of-source-size
    approach: the same stage should pixelate to the same number of blocks
    across the image regardless of the source image's resolution."""
    small = _gradient_png(192)
    large = _gradient_png(768)

    small_result = pixelate(small, PixelStage.STAGE_1)
    large_result = pixelate(large, PixelStage.STAGE_1)

    small_transitions = _count_color_transitions_in_row(small_result, y=0)
    large_transitions = _count_color_transitions_in_row(large_result, y=0)

    expected = STAGE_TARGET_WIDTH[PixelStage.STAGE_1] - 1
    assert small_transitions == expected
    assert large_transitions == expected
