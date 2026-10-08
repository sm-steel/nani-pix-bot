"""Re-exports every model so `Base.metadata` sees all tables for Alembic
autogenerate, no matter which module happens to import `models` first."""

from nani_pix_bot.models.achievement import AchievementClaim as AchievementClaim
from nani_pix_bot.models.achievement import AchievementGrant as AchievementGrant
from nani_pix_bot.models.announcement import AnnouncementOutbox as AnnouncementOutbox
from nani_pix_bot.models.base import Base as Base
from nani_pix_bot.models.bot_settings import BotSettings as BotSettings
from nani_pix_bot.models.clue_purchase import CluePurchase as CluePurchase
from nani_pix_bot.models.currency_config import CurrencyConfig as CurrencyConfig
from nani_pix_bot.models.currency_transfer import CurrencyTransfer as CurrencyTransfer
from nani_pix_bot.models.enums import CurrencyParty as CurrencyParty
from nani_pix_bot.models.enums import CurrencyReason as CurrencyReason
from nani_pix_bot.models.enums import EventType as EventType
from nani_pix_bot.models.enums import GameStatus as GameStatus
from nani_pix_bot.models.enums import OutboxKind as OutboxKind
from nani_pix_bot.models.enums import PeriodType as PeriodType
from nani_pix_bot.models.enums import PixelStage as PixelStage
from nani_pix_bot.models.enums import RevealEffect as RevealEffect
from nani_pix_bot.models.enums import RevealStatus as RevealStatus
from nani_pix_bot.models.event_log import EventLog as EventLog
from nani_pix_bot.models.game import Game as Game
from nani_pix_bot.models.game_guess import GameGuess as GameGuess
from nani_pix_bot.models.game_vote import GameVote as GameVote
from nani_pix_bot.models.mal_link import MalCredentials as MalCredentials
from nani_pix_bot.models.mal_link import PendingMalLink as PendingMalLink
from nani_pix_bot.models.period import PeriodResult as PeriodResult
from nani_pix_bot.models.period import PeriodState as PeriodState
from nani_pix_bot.models.player import Player as Player
from nani_pix_bot.models.reveal_video import RevealVideo as RevealVideo
from nani_pix_bot.models.stage_config import StageConfig as StageConfig
from nani_pix_bot.models.turn_state import TurnState as TurnState
