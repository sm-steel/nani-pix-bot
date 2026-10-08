import subprocess
import tempfile
from pathlib import Path

import pytest
from PIL import Image, ImageChops, ImageDraw, ImageStat

from nani_pix_bot.models.enums import PixelAlgorithm
from nani_pix_bot.services.pixelate import pixelate_image

WIDTHS = (64, 80, 128, 192, 512)


@pytest.fixture(scope="session")
def scene() -> tuple[Image.Image, list[Image.Image]]:
    """A 320x180 synthetic 'screenshot' with edges, a skin-toned blob and
    flat sky, so every effect has detail to work on, plus its 5 stages."""
    image = Image.new("RGB", (320, 180), (120, 170, 220))
    draw = ImageDraw.Draw(image)
    draw.rectangle((0, 120, 320, 180), fill=(60, 160, 60))
    draw.ellipse((180, 40, 230, 95), fill=(240, 200, 170), outline=(30, 20, 20), width=3)
    draw.line((40, 30, 140, 150), fill=(20, 20, 30), width=4)
    stages = [pixelate_image(image, w, PixelAlgorithm.MEDIAN) for w in WIDTHS]
    return image, stages


def last_frame(mp4: bytes, size: tuple[int, int]) -> Image.Image:
    """Decode the final frame via ffmpeg (tests only)."""
    with tempfile.TemporaryDirectory() as tmp:
        src = Path(tmp, "v.mp4")
        src.write_bytes(mp4)
        cmd = ["ffmpeg", "-v", "error", "-sseof", "-0.1", "-i", str(src), "-frames:v", "1",
               "-f", "rawvideo", "-pix_fmt", "rgb24", "pipe:1"]  # fmt: skip
        raw = subprocess.run(cmd, capture_output=True, check=True).stdout  # noqa: S603
    return Image.frombytes("RGB", size, raw[-size[0] * size[1] * 3 :])


def mean_abs_diff(a: Image.Image, b: Image.Image) -> float:
    return sum(ImageStat.Stat(ImageChops.difference(a, b)).mean) / 3
