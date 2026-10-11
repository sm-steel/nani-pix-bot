import time

from PIL import Image

from nani_pix_bot.services.seasons import styles, watercolor


def _sample() -> Image.Image:
    image = Image.new("RGB", (1280, 720), (240, 200, 210))
    for x in range(0, 1280, 64):
        image.paste((40 + x % 200, 90, 160), (x, 0, x + 32, 720))
    return image


def test_it_is_registered() -> None:
    assert styles.STYLES["watercolor"] is watercolor.watercolor


def test_same_size_rgb_and_deterministic() -> None:
    first = watercolor.watercolor(_sample())
    second = watercolor.watercolor(_sample())
    assert first.size == (1280, 720)
    assert first.mode == "RGB"
    assert first.tobytes() == second.tobytes()


def test_it_changes_the_image() -> None:
    assert watercolor.watercolor(_sample()).tobytes() != _sample().tobytes()


def test_it_is_fast_enough() -> None:
    start = time.perf_counter()
    watercolor.watercolor(_sample())
    assert time.perf_counter() - start < 0.5
