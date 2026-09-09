import io

import pytest
from PIL import Image

from nani_pix_bot.models.enums import PixelStage
from nani_pix_bot.services.pixelate import pixelate


def _gradient_png(size: int = 40) -> bytes:
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
    original = _gradient_png(40)

    result = pixelate(original, PixelStage.X10)

    with Image.open(io.BytesIO(result)) as image:
        assert image.size == (40, 40)


@pytest.mark.parametrize(
    ("stage", "divisor"),
    [
        (PixelStage.X10, 10),
        (PixelStage.X8, 8),
        (PixelStage.X5, 5),
        (PixelStage.X2, 2),
    ],
)
def test_pixelate_blocks_match_the_stage_divisor(stage: PixelStage, divisor: int) -> None:
    original = _gradient_png(40)

    result = pixelate(original, stage)

    with Image.open(io.BytesIO(result)) as image:
        rgb = image.convert("RGB")
        pixels = rgb.load()
        assert pixels is not None
        top_left_block = {pixels[x, y] for x in range(divisor) for y in range(divisor)}
        assert len(top_left_block) == 1


def test_pixelate_x10_is_blockier_than_x2() -> None:
    original = _gradient_png(40)

    x10 = pixelate(original, PixelStage.X10)
    x2 = pixelate(original, PixelStage.X2)

    assert _unique_colors(x10) < _unique_colors(x2)
