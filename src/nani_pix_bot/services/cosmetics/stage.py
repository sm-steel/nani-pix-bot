"""Stage-image rendering with the starter's equipped host frame (seasons
spec §3 `host_frame`). Every stage-post site renders through here so the
frame is applied uniformly; the DM preview album and shop clue images are
not stage posts and stay unframed."""

from loguru import logger
from sqlalchemy.orm import Session

from nani_pix_bot.models.enums import CosmeticSlot, PixelAlgorithm
from nani_pix_bot.models.game import Game
from nani_pix_bot.services import pixelate as pixelate_service
from nani_pix_bot.services.cosmetics import frames, inventory


def frame_for(session: Session, game: Game) -> str | None:
    """The game starter's equipped frame look (None for a bot-started game,
    since the bot owns nothing)."""
    return inventory.look(session, game.starter_id, CosmeticSlot.HOST_FRAME)


def stage_png_with(
    image_bytes: bytes, width: int, algorithm: PixelAlgorithm, frame: str | None
) -> bytes:
    """Pixelate, then frame when `frame` is set. For renders outside a
    session (the look is read inside the block and passed in)."""
    png = pixelate_service.pixelate(image_bytes, width, algorithm)
    if frame is None:
        return png
    logger.debug("framing the stage image with {frame!r}", frame=frame)
    return frames.apply_frame(png, frame)


def stage_png(session: Session, game: Game, image_bytes: bytes, width: int) -> bytes:
    return stage_png_with(image_bytes, width, game.pixel_algorithm, frame_for(session, game))
