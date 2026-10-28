"""Stage-image frames (seasons spec §3 `host_frame`): a key → a Pillow
function drawing a border onto the stage image, size unchanged. Season
frames are registered here; "simple_border" is the neutral built-in, the Sakura
paper frames live in paper_frames.py."""

from collections.abc import Callable, Mapping
from types import MappingProxyType

from loguru import logger
from PIL import Image, ImageDraw

from nani_pix_bot.services import stage_image
from nani_pix_bot.services.cosmetics import gold_frame, lantern_frame, night_frames, paper_frames

_SIMPLE_WIDTH_RATIO = 0.03
_SIMPLE_COLOR = (245, 245, 245)


def _simple_border(image: Image.Image) -> Image.Image:
    framed = image.convert("RGB")
    width = max(2, round(min(framed.size) * _SIMPLE_WIDTH_RATIO))
    ImageDraw.Draw(framed).rectangle(
        (0, 0, framed.width - 1, framed.height - 1), outline=_SIMPLE_COLOR, width=width
    )
    return framed


FRAMES: Mapping[str, Callable[[Image.Image], Image.Image]] = MappingProxyType(
    {
        "simple_border": _simple_border,
        "paper": paper_frames.paper,
        "gold_washi": gold_frame.gold_washi,
        "washi": paper_frames.washi,
        "ofuda_strip": night_frames.ofuda_strip,
        "bone_border": night_frames.bone_border,
        "cobweb": night_frames.cobweb,
        "lantern_string": lantern_frame.lantern_string,
    }
)


def apply_frame(png: bytes, frame_key: str) -> bytes:
    draw = FRAMES.get(frame_key)
    if draw is None:
        logger.warning(
            "unknown frame {frame_key!r} — posting the stage unframed", frame_key=frame_key
        )
        return png
    return stage_image.redraw_or_keep(png, draw, "frame", frame_key)
