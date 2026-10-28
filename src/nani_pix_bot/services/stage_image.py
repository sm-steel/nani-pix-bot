"""Redrawing the stage PNG with a frame, a season style or a haunted twist,
which must never block the post."""

from collections.abc import Callable
from io import BytesIO

from loguru import logger
from PIL import Image


def redraw_or_keep(
    png: bytes, draw: Callable[[Image.Image], Image.Image], kind: str, key: str
) -> bytes:
    """`png` run through `draw` and re-encoded; if `draw` raises, the error is logged
    (`kind` is "frame", "style" or "twist", `key` its registry key) and `png`
    comes back as it was."""
    try:
        out = BytesIO()
        draw(Image.open(BytesIO(png))).save(out, format="PNG")
        return out.getvalue()
    except Exception as exc:  # a broken drawing must never block the stage post
        logger.opt(exception=exc).error(
            "{kind} {key!r} failed to draw \u2014 posting the stage without it", kind=kind, key=key
        )
        return png
