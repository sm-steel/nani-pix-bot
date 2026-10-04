"""/sharpen: a player pays currency to advance a normal game's pixelation
stage one step. Pure rules plus the charge-and-advance; the confirm flow and
the group post live in commands/game_flow/sharpen.py."""

import enum
from dataclasses import dataclass

from loguru import logger
from sqlalchemy.orm import Session

from nani_pix_bot.models.enums import CurrencyReason, GameStatus, PixelStage
from nani_pix_bot.models.game import Game
from nani_pix_bot.models.player import Player
from nani_pix_bot.services import game as game_service
from nani_pix_bot.services.economy import config, wallet
from nani_pix_bot.services.economy.config import EconomyKey


class SharpenRefusal(enum.StrEnum):
    NO_GAME = "no_game"  # not ACTIVE, or a different game than the button's
    HARD_MODE = "hard_mode"
    LAST_STAGE = "last_stage"
    SETTER = "setter"
    STALE = "stale"  # the stage or the price moved since the button was made
    INSUFFICIENT = "insufficient"


class SharpenRefusedError(Exception):
    def __init__(self, refusal: SharpenRefusal) -> None:
        super().__init__(refusal.value)
        self.refusal = refusal


def check(game: Game | None, player: Player) -> SharpenRefusal | None:
    """Why `player` can't sharpen `game` right now, or None if they can."""
    if game is None or game.status != GameStatus.ACTIVE:
        return SharpenRefusal.NO_GAME
    if game.hard_mode:
        return SharpenRefusal.HARD_MODE
    if game.current_stage is None or game.current_stage == game_service.STAGE_ORDER[-1]:
        return SharpenRefusal.LAST_STAGE
    if player.telegram_user_id == game.starter_id:
        return SharpenRefusal.SETTER
    return None


def price(session: Session) -> int:
    return config.get_amounts(session)[EconomyKey.SHARPEN]


@dataclass(frozen=True)
class SharpenOffer:
    """What the confirm prompt promised: the stage it was made for and the
    price it showed."""

    stage: PixelStage
    price: int


def sharpen(session: Session, game: Game, player: Player, offer: SharpenOffer) -> int:
    """Charge `player` and advance `game` one stage; returns the price paid.
    Raises SharpenRefusedError (nothing charged) when it isn't allowed, or when
    the stage or price moved since `offer` was made."""
    refusal = check(game, player)
    amount = price(session)
    if refusal is None and (game.current_stage != offer.stage or amount != offer.price):
        refusal = SharpenRefusal.STALE
    if refusal is not None:
        raise SharpenRefusedError(refusal)
    try:
        wallet.debit(
            session, player, amount, wallet.LedgerEntry(CurrencyReason.SHARPEN, game_id=game.id)
        )
    except wallet.InsufficientCurrencyError as error:
        raise SharpenRefusedError(SharpenRefusal.INSUFFICIENT) from error
    # check() excluded the last stage, so this can't end the game UNSOLVED.
    game_service.advance_stage(game)
    logger.info("Player {} paid {} to sharpen game {}", player.telegram_user_id, amount, game.id)
    return amount
