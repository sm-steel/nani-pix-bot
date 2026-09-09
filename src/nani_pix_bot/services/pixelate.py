"""Pillow downscale/upscale pipeline — see MECHANICS.md's "Pixelation
stages" table. No image bytes are ever persisted; callers regenerate a
stage from the original screenshot's bytes on demand."""

import io

from PIL import Image

from nani_pix_bot.models.enums import PixelStage

STAGE_DIVISORS: dict[PixelStage, int] = {
    PixelStage.X10: 10,
    PixelStage.X8: 8,
    PixelStage.X5: 5,
    PixelStage.X2: 2,
}


def pixelate(image_bytes: bytes, stage: PixelStage) -> bytes:
    """Downscale `image_bytes` by the stage's divisor, then upscale it back
    to the original size — the blocky pixelation effect."""
    divisor = STAGE_DIVISORS[stage]
    with Image.open(io.BytesIO(image_bytes)) as source:
        rgb = source.convert("RGB")

    width, height = rgb.size
    small_size = (max(1, width // divisor), max(1, height // divisor))
    small = rgb.resize(small_size, Image.Resampling.NEAREST)
    pixelated = small.resize((width, height), Image.Resampling.NEAREST)

    buffer = io.BytesIO()
    pixelated.save(buffer, format="PNG")
    return buffer.getvalue()
