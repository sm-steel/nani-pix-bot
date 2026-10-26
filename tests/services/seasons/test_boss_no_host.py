"""The boss game has no human host (final review, Critical 1): Nani Pix starts
it, so its end pays her no setter 💠, no host XP or 🪙 and no period host
points, and the season pass never syncs her."""

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select

from nani_pix_bot.models.enums import CurrencyReason, GameStatus, PeriodType
from nani_pix_bot.models.game import Game, bot_hosted_clause
from nani_pix_bot.models.season import SeasonXp
from nani_pix_bot.services import game as game_service
from nani_pix_bot.services.achievements import periods
from nani_pix_bot.services.economy import earning, wallet
from nani_pix_bot.services.seasons import consumer, hexes, xp
from tests.seasons_fixtures import WINNER

BOT = 999


@pytest.fixture
def synced(monkeypatch: pytest.MonkeyPatch) -> list[int]:
    """Every player season_pass.sync ran for."""
    seen: list[int] = []
    monkeypatch.setattr(
        consumer.season_pass, "sync", lambda _s, _season, _run, pid: seen.append(pid)
    )
    return seen


@pytest.fixture
def boss(finished_game) -> Game:
    return finished_game(GameStatus.ACTIVE, guessers=(WINNER,), starter_id=BOT, boss_game=True)


def _nothing_for_the_bot(session, game: Game, synced: list[int]) -> None:
    assert session.scalars(select(SeasonXp).where(SeasonXp.player_id == BOT)).all() == []
    assert game.season_id is not None
    assert BOT not in {p.player_id for p in xp.standings(session, game.season_id)}
    assert wallet.balance(session, BOT, season_id=game.season_id) == 0
    assert wallet.balance(session, BOT) == 0
    assert BOT not in synced


def test_bot_hosted_covers_hard_mode_and_the_boss(session, boss, finished_game) -> None:
    hard = finished_game(GameStatus.WON, hard_mode=True)
    plain = finished_game(GameStatus.WON)
    assert boss.bot_hosted
    assert hard.bot_hosted
    assert not plain.bot_hosted
    hosted = session.scalars(select(Game.id).where(bot_hosted_clause())).all()
    assert set(hosted) == {boss.id, hard.id}


def test_a_won_boss_pays_the_bot_nothing(session, boss, synced) -> None:
    game_service.force_win(session, boss, winner_id=WINNER)
    paid = earning.award_win(session, boss, winner_id=WINNER)

    assert paid.setter == 0
    assert (
        wallet.game_total(session, player_id=BOT, game_id=boss.id, reason=CurrencyReason.SETTER)
        == 0
    )
    _nothing_for_the_bot(session, boss, synced)
    assert WINNER in synced  # the winner is still paid
    won = periods.win_event(session, boss.id)
    assert won is not None
    assert won.data["boss_game"] is True
    period = periods.Period(
        PeriodType.WEEK,
        "w",
        datetime.now(UTC) - timedelta(days=1),
        datetime.now(UTC) + timedelta(days=1),
    )
    assert [s.player_id for s in periods.score([won], period)] == [WINNER]


def test_an_unsolved_boss_pays_the_bot_nothing(session, boss, synced) -> None:
    game_service.force_unsolved(boss, cause="test")
    session.flush()
    _nothing_for_the_bot(session, boss, synced)


def test_hosting_the_boss_is_not_playing_the_season(session, boss) -> None:
    assert not hexes.played_in_season(session, boss.season_id, BOT)
    assert hexes.played_in_season(session, boss.season_id, WINNER)
