from datetime import UTC, datetime

import pytest

from nani_pix_bot.models.enums import GameStatus
from nani_pix_bot.models.game import Game
from nani_pix_bot.models.player import Player
from nani_pix_bot.services import game as game_service
from nani_pix_bot.services.game import state
from nani_pix_bot.services.seasons import haunting
from tests.seasons_fixtures import FixedRng


@pytest.fixture(autouse=True)
def _season_runs(fake_runs):
    """The shared fake runs, applied to every test in this module."""


def _setup_game(session) -> Game:
    session.add(Player(telegram_user_id=1))
    game = Game(starter_id=1, status=GameStatus.SETUP, title_english="Clannad", source="manual")
    session.add(game)
    session.flush()
    return game


def _freeze(monkeypatch, at: datetime) -> None:
    class _Frozen(datetime):
        @classmethod
        def now(cls, tz=None):
            return at.astimezone(tz) if tz else at.replace(tzinfo=None)

    monkeypatch.setattr(state, "datetime", _Frozen)


def test_activation_rolls_and_stores_the_twist(session, haunted_season, monkeypatch) -> None:
    game = _setup_game(session)
    monkeypatch.setattr(haunting, "_rng", lambda: FixedRng(randrange=0, choice=3))
    game_service.activate_game(session, game)
    assert game.haunt_twist == "kyokotsu_xray"
    assert game.event_day is False


@pytest.mark.parametrize(
    ("at", "expected"),
    [
        (datetime(2026, 10, 31, 20, 59, tzinfo=UTC), True),  # 23:59 MSK
        (datetime(2026, 10, 31, 21, 0, tzinfo=UTC), False),  # 00:00 MSK, Nov 1
    ],
)
def test_activation_at_2359_and_0000(
    session, haunted_season, set_group_tz, monkeypatch, at, expected
) -> None:
    set_group_tz("Europe/Moscow")
    _freeze(monkeypatch, at)
    monkeypatch.setattr(haunting, "_rng", lambda: FixedRng(randrange=99, choice=1))
    game = _setup_game(session)
    game_service.activate_game(session, game)
    assert game.event_day is expected
    assert (game.haunt_twist is not None) is expected


def test_activation_logs_a_haunting(session, haunted_season, monkeypatch, log_records) -> None:
    monkeypatch.setattr(haunting, "_rng", lambda: FixedRng(randrange=3, choice=1))
    game = _setup_game(session)
    game_service.activate_game(session, game)
    line = next(r for r in log_records if r.message.startswith("haunted"))
    assert line.level == "INFO"
    assert line.extra["twist"] == "tanuki_leaf"
    assert line.extra["chance"] == 25
    assert line.extra["event_day"] is False


def test_activation_logs_a_clean_roll(session, haunted_season, monkeypatch, log_records) -> None:
    monkeypatch.setattr(haunting, "_rng", lambda: FixedRng(randrange=60))
    game = _setup_game(session)
    game_service.activate_game(session, game)
    line = next(r for r in log_records if r.message.startswith("not haunted"))
    assert line.level == "INFO"
    assert (line.extra["roll"], line.extra["chance"]) == (60, 25)
    assert game.haunt_twist is None


def test_an_untagged_game_is_not_rolled(session, log_records) -> None:
    game = _setup_game(session)
    game_service.activate_game(session, game)
    assert game.haunt_twist is None
    assert game.event_day is False
    assert not any("haunted" in r.message or "activated on" in r.message for r in log_records)


def test_a_season_without_haunting_never_rolls(session, active_season, log_records) -> None:
    game = _setup_game(session)
    game_service.activate_game(session, game)
    assert game.season_id == active_season.id
    assert game.haunt_twist is None
    assert game.event_day is False
    assert not any("haunted" in r.message or "activated on" in r.message for r in log_records)
