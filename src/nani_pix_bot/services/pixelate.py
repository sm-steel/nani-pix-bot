"""Pillow downscale/upscale pipeline — see MECHANICS.md's "Pixelation
stages" table. No image bytes are ever persisted; callers regenerate a
stage's image from the original screenshot's bytes on demand.

Downscales to a fixed *target width*, not a divisor of the source
resolution — a divisor barely pixelates a high-resolution screenshot
(e.g. ÷10 of a 2560px-wide image is still 256px wide, plenty detailed).
A fixed target keeps difficulty independent of screenshot resolution: a
12px-wide image reads as pure color blobs whether the original was
1280px or 3840px wide.

Deliberately takes a raw width rather than a `PixelStage` — which width
applies to which stage is admin-configurable and DB-backed (see
services/settings/stage_config.py), so callers resolve that themselves and this
module stays a pure, DB-agnostic image-processing function.
"""

import io

from loguru import logger
from PIL import Image


def pixelate(image_bytes: bytes, target_width: int) -> bytes:
    """Downscale `image_bytes` to `target_width` (preserving aspect
    ratio), then upscale back to the original size — the blocky
    pixelation effect."""
    with Image.open(io.BytesIO(image_bytes)) as source:
        rgb = source.convert("RGB")

    width, height = rgb.size
    scale = target_width / width
    small_size = (target_width, max(1, round(height * scale)))
    small = rgb.resize(small_size, Image.Resampling.NEAREST)
    pixelated = small.resize((width, height), Image.Resampling.NEAREST)

    buffer = io.BytesIO()
    pixelated.save(buffer, format="PNG")
    result = buffer.getvalue()
    logger.debug(
        "Pixelated {}x{} image to target width {} -> {} bytes",
        width,
        height,
        target_width,
        len(result),
    )
    return result
