"""Synthetic screenshot 'frames' for tests that need real, distinguishable
images — the fingerprint tests and the screenshot-clue picker's."""

import io
import random

from PIL import Image, ImageDraw


def frame(seed: int, size: tuple[int, int] = (640, 360)) -> Image.Image:
    """A background gradient with a few random shapes, so two seeds differ
    the way two shots of one show do."""
    rng = random.Random(seed)  # noqa: S311 - deterministic test rng, not crypto
    image = Image.new("RGB", size)
    draw = ImageDraw.Draw(image)
    width, height = size
    for x in range(width):
        shade = int(255 * x / width)
        draw.line([(x, 0), (x, height)], fill=(shade, 80, 255 - shade))
    for _ in range(6):
        x0, y0 = rng.randrange(width), rng.randrange(height)
        x1, y1 = x0 + rng.randrange(40, 240), y0 + rng.randrange(40, 200)
        colour = (rng.randrange(256), rng.randrange(256), rng.randrange(256))
        draw.rectangle([x0, y0, x1, y1], fill=colour)
    return image


def encode(image: Image.Image, fmt: str = "PNG", **kwargs) -> bytes:
    buffer = io.BytesIO()
    image.save(buffer, format=fmt, **kwargs)
    return buffer.getvalue()


def frame_bytes(seed: int) -> bytes:
    return encode(frame(seed))
