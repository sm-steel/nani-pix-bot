from datetime import UTC, datetime, timedelta

import pytest

from nani_pix_bot.models.enums import CurrencyReason, GameStatus, PixelStage, SeasonStatus
from nani_pix_bot.models.game import Game
from nani_pix_bot.models.game_guess import GameGuess
from nani_pix_bot.models.player import Player
from nani_pix_bot.models.season import SeasonSchedule
from nani_pix_bot.services.economy import wallet
from nani_pix_bot.services.economy.wallet import LedgerEntry

NOW = datetime(2026, 10, 30, 12, 0, tzinfo=UTC)


@pytest.fixture(autouse=True)
def _season_runs(fake_runs):
    """The shared fake runs, applied to every test in this folder."""


def naive(at: datetime) -> datetime:
    return at.replace(tzinfo=None)


def make_game(session, season_id, starter, *, activated: bool = True) -> Game:
    game = Game(
        starter_id=starter,
        status=GameStatus.ACTIVE,
        current_stage=PixelStage.STAGE_1,
        title_english="Clannad",
        season_id=season_id,
        activated_at=naive(NOW) if activated else None,
    )
    session.add(game)
    session.flush()
    return game


def make_hex_season(session) -> SeasonSchedule:
    """Players 1-4, a game hosted by 4 that 1 and 2 guessed in, 200 🪙 each."""
    season = SeasonSchedule(
        run_id="demo_3",
        start_at=NOW - timedelta(days=1),
        end_at=NOW + timedelta(days=9),
        status=SeasonStatus.ACTIVE,
        created_by=9,
    )
    session.add(season)
    session.add_all([Player(telegram_user_id=u) for u in (1, 2, 3, 4)])
    session.flush()
    game = make_game(session, season.id, 4)
    session.add_all([GameGuess(game_id=game.id, player_id=u, text="x", stage=1) for u in (1, 2)])
    session.flush()
    for user in (1, 2):
        entry = LedgerEntry(CurrencyReason.SEASON_WIN, season_id=season.id, coins=True)
        wallet.credit(session, session.get(Player, user), 200, entry)
    return season


@pytest.fixture
def hex_season(session) -> SeasonSchedule:
    return make_hex_season(session)
