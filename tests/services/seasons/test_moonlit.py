import time

import pytest
from PIL import Image

from nani_pix_bot.models.enums import PixelAlgorithm
from nani_pix_bot.services.cosmetics import previews
from nani_pix_bot.services.pixelate.algorithms import pixelate_image
from nani_pix_bot.services.seasons import moonlit, styles


def _sample() -> Image.Image:
    image = Image.new("RGB", (1280, 720), (240, 200, 210))
    for x in range(0, 1280, 64):
        image.paste((40 + x % 200, 90, 160), (x, 0, x + 32, 720))
    return image


def _pixel(image: Image.Image, x: int, y: int) -> tuple[int, int, int]:
    r, g, b = image.crop((x, y, x + 1, y + 1)).tobytes()
    return r, g, b


def test_it_is_registered() -> None:
    assert styles.STYLES["moonlit"] is moonlit.moonlit


def test_same_size_rgb_and_deterministic() -> None:
    first = moonlit.moonlit(_sample())
    second = moonlit.moonlit(_sample())
    assert first.size == (1280, 720)
    assert first.mode == "RGB"
    assert first.tobytes() == second.tobytes()


@pytest.mark.parametrize("width", previews.STAGE_WIDTHS)
def test_the_centre_is_untouched(width: int) -> None:
    stage = pixelate_image(_sample(), width, PixelAlgorithm.MEDIAN)
    out = moonlit.moonlit(stage)
    box = (int(1280 * 0.35), int(720 * 0.35), int(1280 * 0.65), int(720 * 0.65))
    assert out.crop(box).tobytes() == stage.convert("RGB").crop(box).tobytes()


def test_the_corners_turn_indigo_and_darker() -> None:
    out = moonlit.moonlit(Image.new("RGB", (1280, 720), (128, 128, 128)))
    r, g, b = _pixel(out, 1279, 0)
    assert b > r
    assert r + g + b < 384


def test_the_moon_corner_is_lighter_than_the_opposite_one() -> None:
    out = moonlit.moonlit(Image.new("RGB", (1280, 720), (128, 128, 128)))
    # beside the sprites: just inside the left edge, near the moon vs near the bottom
    top = _pixel(out, 2, 300)
    bottom = _pixel(out, 2, 717)
    assert sum(top) > sum(bottom)


def _differs(graded: Image.Image, with_art: Image.Image, box: tuple[int, int, int, int]) -> bool:
    return graded.crop(box).tobytes() != with_art.crop(box).tobytes()


def test_the_top_corners_have_art_and_the_bottom_ones_do_not() -> None:
    grey = Image.new("RGB", (1280, 720), (128, 128, 128))
    out = moonlit.moonlit(grey)
    graded = moonlit._grade(grey)
    size = int(1280 * moonlit.CORNER_WIDTH)
    assert _differs(graded, out, (0, 0, size, size // 2))
    assert _differs(graded, out, (1280 - size, 0, 1280, size // 2))
    assert not _differs(graded, out, (0, 720 - size // 2, size, 720))
    assert not _differs(graded, out, (1280 - size, 720 - size // 2, 1280, 720))


def test_a_missing_sprite_is_logged_once_and_the_stage_still_renders(
    monkeypatch: pytest.MonkeyPatch, log_records
) -> None:
    def gone(name: str) -> Image.Image:
        raise FileNotFoundError(name)

    monkeypatch.setattr(moonlit.sprites, "sprite", gone)
    monkeypatch.setattr(moonlit, "_missing_logged", set())
    grey = Image.new("RGB", (640, 360), (128, 128, 128))
    first = moonlit.moonlit(grey)
    moonlit.moonlit(grey)
    assert first.tobytes() == moonlit._grade(grey).tobytes()
    errors = [r for r in log_records if r.level == "ERROR"]
    assert len(errors) == 2  # one per missing sprite, not per render


def test_it_is_fast_enough() -> None:
    moonlit.moonlit(_sample())
    start = time.perf_counter()
    moonlit.moonlit(_sample())
    assert time.perf_counter() - start < 0.5


def test_the_per_size_mask_is_a_bounded_cache() -> None:
    assert moonlit._layers.cache_info().maxsize == moonlit.LAYER_CACHE_SIZE


@pytest.mark.parametrize("size", [(180, 320), (64, 36)])
def test_moonlit_handles_portrait_and_tiny_images(size: tuple[int, int]) -> None:
    out = moonlit.moonlit(Image.new("RGB", size, (90, 120, 60)))
    assert out.size == size
    assert out.mode == "RGB"
