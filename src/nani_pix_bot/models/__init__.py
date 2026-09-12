"""Re-exports every model so `Base.metadata` sees all tables for Alembic
autogenerate, no matter which module happens to import `models` first."""

from nani_pix_bot.models.base import Base
from nani_pix_bot.models.bot_settings import BotSettings
from nani_pix_bot.models.enums import GameStatus, PixelStage
from nani_pix_bot.models.game import Game
from nani_pix_bot.models.player import Player
from nani_pix_bot.models.stage_config import StageConfig
from nani_pix_bot.models.turn_state import TurnState

__all__ = [
    "Base",
    "BotSettings",
    "Game",
    "GameStatus",
    "PixelStage",
    "Player",
    "StageConfig",
    "TurnState",
]
