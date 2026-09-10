"""Pillow downscale/upscale pipeline — see MECHANICS.md's "Pixelation
stages" table. No image bytes are ever persisted; callers regenerate a
stage from the original screenshot's bytes on demand.

Stages downscale to a fixed *target width*, not a divisor of the source
resolution — a divisor barely pixelates a high-resolution screenshot
(e.g. ÷10 of a 2560px-wide image is still 256px wide, plenty detailed).
A fixed target keeps stage difficulty independent of screenshot
resolution: a 12px-wide image reads as pure color blobs whether the
original was 1280px or 3840px wide.
"""

import io

from PIL import Image

from nani_pix_bot.models.enums import PixelStage

# Blockiest to clearest. Chosen by eye against real screenshots — see the
# discussion on issue-tracked pixelation tuning.
STAGE_TARGET_WIDTH: dict[PixelStage, int] = {
    PixelStage.X10: 12,
    PixelStage.X8: 24,
    PixelStage.X5: 48,
    PixelStage.X2: 64,
}


def pixelate(image_bytes: bytes, stage: PixelStage) -> bytes:
    """Downscale `image_bytes` to the stage's target width (preserving
    aspect ratio), then upscale back to the original size — the blocky
    pixelation effect."""
    target_width = STAGE_TARGET_WIDTH[stage]
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
