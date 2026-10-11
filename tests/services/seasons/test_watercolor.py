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


def test_the_per_size_layers_are_bounded_caches() -> None:
    for layer in (watercolor._grain, watercolor._blots, watercolor._edge_mask):
        assert layer.cache_info().maxsize == watercolor.LAYER_CACHE_SIZE, layer.__name__


def test_an_evicted_size_renders_the_same_again() -> None:
    first = watercolor.watercolor(_sample()).tobytes()
    for width in range(watercolor.LAYER_CACHE_SIZE + 1):  # push 1280x720 out of every cache
        watercolor.watercolor(Image.new("RGB", (100 + width, 60)))
    assert watercolor.watercolor(_sample()).tobytes() == first
