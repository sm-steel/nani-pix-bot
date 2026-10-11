"""Stage-image rendering with the season's visual style and the starter's
equipped host frame (seasons spec §3 `host_frame`, §5 style). Every
stage-post site renders through here so both are applied uniformly: pixelate,
then the season style, then the frame on top (the frame stays crisp). The DM
preview album and shop clue images are not stage posts and stay unframed."""

from loguru import logger
from sqlalchemy.orm import Session

from nani_pix_bot.models.enums import CosmeticSlot, PixelAlgorithm
from nani_pix_bot.models.game import Game
from nani_pix_bot.services import pixelate as pixelate_service
from nani_pix_bot.services.cosmetics import frames, inventory
from nani_pix_bot.services.seasons import styles


def frame_for(session: Session, game: Game) -> str | None:
    """The game starter's equipped frame look (None for a bot-started game,
    since the bot owns nothing)."""
    return inventory.look(session, game.starter_id, CosmeticSlot.HOST_FRAME)


def stage_png_with(
    image_bytes: bytes,
    width: int,
    algorithm: PixelAlgorithm,
    frame: str | None,
    style: str | None = None,
) -> bytes:
    """Pixelate, then the season style, then the frame on top. For renders
    outside a session (the keys are read inside it and passed in)."""
    png = pixelate_service.pixelate(image_bytes, width, algorithm)
    if style is not None:
        logger.debug("styling the stage image with {style!r}", style=style)
        png = styles.apply_style(png, style)
    if frame is None:
        return png
    logger.debug("framing the stage image with {frame!r}", frame=frame)
    return frames.apply_frame(png, frame)


def stage_png(session: Session, game: Game, image_bytes: bytes, width: int) -> bytes:
    style = styles.style_for(session, game)
    if style is not None:
        logger.info("stage image in the season style {style!r}", style=style, game_id=game.id)
    return stage_png_with(image_bytes, width, game.pixel_algorithm, frame_for(session, game), style)
