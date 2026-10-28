"""Committed RGBA sprites for the season overlays and frames: loaded once,
cached, resized to a height on demand. Runs in the reveal worker too:
module-level functions, stdlib logging only (no loguru: records reach loguru
through logging_config's InterceptHandler on the bot side)."""

import logging
from functools import cache, lru_cache
from pathlib import Path

from PIL import Image

SPRITE_DIR = Path(__file__).resolve().parent.parent / "assets" / "sprites"

_logged_missing: set[str] = set()


@cache
def sprite(name: str) -> Image.Image:
    """The sprite `name` as RGBA; FileNotFoundError when it isn't committed.
    The image is shared: callers must not draw on it."""
    with Image.open(SPRITE_DIR / name) as image:
        return image.convert("RGBA")


@lru_cache(maxsize=32)
def sprite_height(name: str, height: int) -> Image.Image:
    """The sprite scaled to `height` pixels tall, keeping its aspect."""
    source = sprite(name)
    width = max(1, round(source.width * height / source.height))
    return source.resize((width, height), Image.Resampling.LANCZOS)


def silhouette(image: Image.Image, color: tuple[int, int, int]) -> Image.Image:
    """The same alpha as `image`, painted one flat colour."""
    flat = Image.new("RGBA", image.size, (*color, 0))
    flat.putalpha(image.convert("RGBA").getchannel("A"))
    return flat


def log_missing(name: str, fallback: str) -> None:
    """Log that the committed sprite `name` is missing, once per process and sprite;
    `fallback` says how the caller renders without it."""
    if name not in _logged_missing:
        _logged_missing.add(name)
        logging.getLogger(__name__).error("art %s is missing — %s", name, fallback)


def sprite_or_none(name: str, height: int, fallback: str) -> Image.Image | None:
    """The sprite `name` scaled to `height`, or None when its file is missing: the caller
    renders without it (`fallback` says how) and the gap is logged once (`log_missing`)."""
    try:
        return sprite_height(name, height)
    except OSError:
        log_missing(name, fallback)
        return None
