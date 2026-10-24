"""Stage-image rendering with the season's visual style, the starter's
equipped host frame (seasons spec §3 `host_frame`, §5 style) and Nani Pix
(issue #461). Every stage-post site renders through here so all three are
applied uniformly: pixelate, then the season style, then the frame, then
Nani on top of everything (both stay crisp). The DM preview album and shop
clue images are not stage posts and stay unframed."""

from typing import NamedTuple

from loguru import logger
from sqlalchemy.orm import Session

from nani_pix_bot.models.enums import CosmeticSlot, PixelAlgorithm
from nani_pix_bot.models.game import Game
from nani_pix_bot.seasons.mechanics import HauntTwist
from nani_pix_bot.services import game as game_service
from nani_pix_bot.services import mascot
from nani_pix_bot.services import pixelate as pixelate_service
from nani_pix_bot.services.cosmetics import costume, frames, inventory
from nani_pix_bot.services.game import HardModeTurnProgress, StageProgress
from nani_pix_bot.services.mascot import Pose
from nani_pix_bot.services.seasons import haunting, modifiers, styles


class Haunt(NamedTuple):
    """The haunted twist a stage render wears; `last` is the final stage, where
    the fading twists clear up."""

    twist: HauntTwist
    last: bool


class SeasonLook(NamedTuple):
    """What the season does to a stage image: its style key and the haunted
    twist (the twist goes under the style)."""

    style: str | None = None
    haunt: Haunt | None = None


def frame_for(session: Session, game: Game) -> str | None:
    """The game starter's equipped frame look (None for a bot-started game,
    since the bot owns nothing)."""
    return inventory.look(session, game.starter_id, CosmeticSlot.HOST_FRAME)


def stage_png_with(
    image_bytes: bytes,
    width: int,
    algorithm: PixelAlgorithm,
    frame: str | None,
    look: SeasonLook | None = None,
) -> bytes:
    """Pixelate, then the haunted twist, then the season style, then the frame
    on top. For renders outside a session (the keys are read inside it and
    passed in). Callers add Nani on top of the result (`mascot.add_to_png`)."""
    png = pixelate_service.pixelate(image_bytes, width, algorithm)
    if look is not None and look.haunt is not None:
        png = haunting.apply_twist(png, look.haunt.twist, last=look.haunt.last)
    if look is not None and look.style is not None:
        logger.debug("styling the stage image with {style!r}", style=look.style)
        png = styles.apply_style(png, look.style)
    if frame is None:
        return png
    logger.debug("framing the stage image with {frame!r}", frame=frame)
    return frames.apply_frame(png, frame)


def _progress(session: Session, game: Game) -> StageProgress | HardModeTurnProgress:
    return (
        game_service.hard_mode_turn_progress(game)
        if game.hard_mode
        else game_service.stage_progress(session, game)
    )


def stage_pose(session: Session, game: Game, *, overthrow: bool = False) -> Pose:
    """Nani's pose for `game`'s current stage (or HARD MODE turn); `overthrow`
    for turn 1 of a game the bot took by overthrow."""
    progress = _progress(session, game)
    return mascot.stage_pose(progress.number, progress.total, overthrow=overthrow)


def season_style(session: Session, game: Game) -> str | None:
    """The game's season style key, logged at INFO when there is one. Every
    stage render reads it through here (stage_png, /status) so the decision
    is in the log exactly once per render."""
    style = styles.style_for(session, game)
    if style is not None:
        logger.info("stage image in the season style {style!r}", style=style, game_id=game.id)
    return style


def stage_twist(session: Session, game: Game, number: int) -> HauntTwist | None:
    """The haunted twist stage `number` wears, logged at INFO when there is one,
    like `season_style`."""
    twist = haunting.twist_for_stage(modifiers.season_of(session, game), game, number)
    if twist is not None:
        logger.info(
            "stage image haunted with {twist}", twist=twist.value, stage=number, game_id=game.id
        )
    return twist


def costume_for(session: Session, game: Game) -> str | None:
    """The costume Nani wears on the game's stage images, logged at INFO when
    there is one, like `season_style`."""
    key = costume.for_game(session, game)
    if key is not None:
        logger.info(
            "Nani on the stage image in the {costume!r} costume", costume=key, game_id=game.id
        )
    return key


def stage_png(
    session: Session, game: Game, image_bytes: bytes, width: int, overthrow: bool = False
) -> bytes:
    """The stage image as posted: twisted, styled, framed, Nani on top in her
    pose and costume."""
    progress = _progress(session, game)
    twist = stage_twist(session, game, progress.number)
    png = stage_png_with(
        image_bytes,
        width,
        game.pixel_algorithm,
        frame_for(session, game),
        SeasonLook(
            season_style(session, game),
            Haunt(twist, progress.number == progress.total) if twist is not None else None,
        ),
    )
    pose = mascot.stage_pose(progress.number, progress.total, overthrow=overthrow)
    return mascot.add_to_png(png, pose, costume_for(session, game))
