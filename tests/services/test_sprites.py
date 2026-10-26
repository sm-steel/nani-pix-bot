from collections.abc import Iterator
from pathlib import Path

import pytest
from PIL import Image

from nani_pix_bot.services import sprites

MAX_SPRITE_BYTES = 400 * 1024
COMMITTED: tuple[str, ...] = (
    "moonlit/top_left.png",
    "moonlit/top_right.png",
)  # every task that commits art adds its file names here


@pytest.fixture
def sprite_dir(tmp_path, monkeypatch) -> Iterator[Path]:
    monkeypatch.setattr(sprites, "SPRITE_DIR", tmp_path)
    sprites.sprite.cache_clear()
    sprites.sprite_height.cache_clear()
    yield tmp_path
    sprites.sprite.cache_clear()
    sprites.sprite_height.cache_clear()


def test_sprite_is_rgba_and_cached(sprite_dir) -> None:
    Image.new("RGB", (100, 200), (9, 9, 9)).save(sprite_dir / "x.png")
    first = sprites.sprite("x.png")
    assert first.mode == "RGBA"
    assert sprites.sprite("x.png") is first


def test_sprite_height_keeps_the_aspect(sprite_dir) -> None:
    Image.new("RGBA", (100, 200)).save(sprite_dir / "x.png")
    scaled = sprites.sprite_height("x.png", 50)
    assert scaled.size == (25, 50)
    assert sprites.sprite_height("x.png", 50) is scaled


def test_a_missing_sprite_raises_file_not_found(sprite_dir) -> None:
    with pytest.raises(FileNotFoundError):
        sprites.sprite("nope.png")


def test_silhouette_keeps_alpha_and_flattens_colour() -> None:
    src = Image.new("RGBA", (2, 1))
    src.putpixel((0, 0), (200, 10, 10, 255))
    src.putpixel((1, 0), (10, 200, 10, 90))
    out = sprites.silhouette(src, (1, 2, 3))
    assert out.getpixel((0, 0)) == (1, 2, 3, 255)
    assert out.getpixel((1, 0)) == (1, 2, 3, 90)


@pytest.mark.parametrize("name", COMMITTED)
def test_every_sprite_is_committed_rgba(name: str) -> None:
    assert sprites.sprite(name).mode == "RGBA"
    assert (sprites.SPRITE_DIR / name).stat().st_size <= MAX_SPRITE_BYTES
