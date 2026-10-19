"""Nani Pix in the corner of every game image (issue #461): which pose a
moment gets, where she goes, and compositing her on top of a finished image
(after pixelation, style and frame, so she always stays sharp). Pure image
work, no Telegram. A failure never blocks a post: the image goes out without
her and the failure is logged."""

import enum
import functools
import io
from pathlib import Path

from loguru import logger
from PIL import Image

POSE_DIR = Path(__file__).resolve().parent.parent / "assets" / "mascot"

SHARE = 0.16  # her width, as a share of the image's width
MARGIN = 0.02  # gap to the right and bottom edges, as a share of the image's width
SOURCE_WIDTH = 384  # the pose files' width: she is never drawn wider
MIN_WIDTH = 8  # narrower than this she'd be a smudge, so she's left out
JPEG_QUALITY = 92  # re-encoded photos: Telegram recompresses them anyway


class Pose(enum.Enum):
    CAST = "cast"  # stage 1, and the reveal video before the picture is clear
    NOPE = "nope"  # the stages in between
    NANI = "nani"  # the last stage, and a reveal posted when a HARD MODE vote opens
    WIN = "win"  # a won round's reveal
    OOPS = "oops"  # every other ending's reveal
    SLY = "sly"  # stage 1 of a game she took by overthrow


def stage_pose(number: int, total: int, *, overthrow: bool = False) -> Pose:
    """The pose on stage (or HARD MODE turn) `number` of `total`."""
    if number >= total:
        return Pose.NANI
    if number == 1:
        return Pose.SLY if overthrow else Pose.CAST
    return Pose.NOPE


def ending_pose(*, won: bool, vote_open: bool = False) -> Pose:
    """The pose on a reveal: the HARD MODE vote's is posted before anyone knows
    whether it will crown a winner."""
    if vote_open:
        return Pose.NANI
    return Pose.WIN if won else Pose.OOPS


@functools.cache
def _source(pose: Pose) -> Image.Image:
    with Image.open(POSE_DIR / f"{pose.value}.png") as img:
        return img.convert("RGBA")


@functools.lru_cache(maxsize=64)
def sized(pose: Pose, width: int) -> Image.Image:
    """`pose` at `width` px wide, its aspect kept. Decoded once per process, then
    cached per width (a read-only asset cache, not feature state)."""
    source = _source(pose)
    if width == source.width:
        return source
    height = round(source.height * width / source.width)
    return source.resize((width, height), Image.Resampling.LANCZOS)


def box(size: tuple[int, int], pose: Pose) -> tuple[int, int, int, int]:
    """(x, y, width, height) of Nani in an image of `size`: bottom-right, SHARE of
    its width, MARGIN from the edges, shrunk to fit a short image. Width 0: too
    small to draw her."""
    image_w, image_h = size
    margin = round(image_w * MARGIN)
    source = _source(pose)
    width = min(round(image_w * SHARE), SOURCE_WIDTH)
    room = image_h - 2 * margin
    if source.height * width > room * source.width:
        width = room * source.width // source.height
    if width < MIN_WIDTH:
        return 0, 0, 0, 0
    height = round(source.height * width / source.width)
    return image_w - width - margin, image_h - height - margin, width, height


def compose(image: Image.Image, pose: Pose) -> Image.Image:
    """An RGB copy of `image` with Nani on top. On any failure: logged at ERROR,
    and the copy comes back without her."""
    try:
        x, y, width, _ = box(image.size, pose)
        if width == 0:
            return image.convert("RGB")
        canvas = image.convert("RGBA")
        canvas.alpha_composite(sized(pose, width), (x, y))
    except Exception as exc:
        logger.opt(exception=exc).error(
            "couldn't add Nani ({pose}) to the image — it goes out without her",
            pose=pose.value,
        )
        return image.convert("RGB")
    logger.debug("added Nani ({pose}) at {width}px", pose=pose.value, width=width)
    return canvas.convert("RGB")


def _add(data: bytes, pose: Pose, fmt: str, **save: object) -> bytes:
    """compose() on encoded image bytes, re-encoded as `fmt`; bytes that can't
    be decoded or encoded go out unchanged (logged at ERROR)."""
    try:
        with Image.open(io.BytesIO(data)) as image:
            out = compose(image, pose)
        buf = io.BytesIO()
        out.save(buf, fmt, **save)
    except Exception as exc:
        logger.opt(exception=exc).error(
            "couldn't add Nani ({pose}) to the image — it goes out without her",
            pose=pose.value,
        )
        return data
    return buf.getvalue()


def add_to_png(png: bytes, pose: Pose) -> bytes:
    """compose() for a rendered PNG (stage images, previews), PNG out."""
    return _add(png, pose, "PNG")


def add_to_jpeg(image: bytes, pose: Pose) -> bytes:
    """compose() for a stored photo in any format (the sharp reveal, clue
    screenshots), JPEG out."""
    return _add(image, pose, "JPEG", quality=JPEG_QUALITY, optimize=True)


def still_png(pose: Pose, frame_size: tuple[int, int]) -> tuple[bytes, int]:
    """Nani as an RGBA PNG sized for a video frame of `frame_size`, and the margin
    to place her with (bottom-right), for an ffmpeg overlay."""
    _, _, width, _ = box(frame_size, pose)
    buf = io.BytesIO()
    sized(pose, max(width, 1)).save(buf, "PNG")
    return buf.getvalue(), round(frame_size[0] * MARGIN)
