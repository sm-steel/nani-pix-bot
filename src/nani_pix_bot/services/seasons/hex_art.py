"""The art the hex moment cards show: one picture per hex kind, plus the Omamori's."""

from functools import cache
from pathlib import Path

from loguru import logger
from PIL import Image

ICON_DIR = Path(__file__).resolve().parents[2] / "assets" / "icons"
HEX_ART_DIR = ICON_DIR / "hex"
OMAMORI = "omamori"


def _path(name: str) -> Path:
    return ICON_DIR / f"{OMAMORI}.png" if name == OMAMORI else HEX_ART_DIR / f"{name}.png"


@cache
def art(name: str) -> Image.Image | None:
    """The RGBA art for a HexKind value or OMAMORI, decoded once; None (logged
    once) when its PNG is missing, so the caller posts the plain line instead."""
    path = _path(name)
    try:
        with Image.open(path) as source:
            return source.convert("RGBA")
    except FileNotFoundError:
        logger.error("no hex art {name!r} at {path}", name=name, path=str(path))
        return None
