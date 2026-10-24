from datetime import UTC, datetime, timedelta

import pytest

from nani_pix_bot.models.bot_settings import BotSettings
from nani_pix_bot.models.enums import EventType, GameStatus, PixelStage, SeasonStatus
from nani_pix_bot.models.game import Game
from nani_pix_bot.models.game_guess import GameGuess
from nani_pix_bot.models.player import Player
from nani_pix_bot.models.season import SeasonSchedule
from nani_pix_bot.seasons import registry
from nani_pix_bot.services import events, settings

STARTER, WINNER = 2, 1
NOW = datetime(2026, 11, 1, tzinfo=UTC)


@pytest.fixture
def fake_runs(monkeypatch):
    """Not autouse: this module is a suite-wide plugin, so each season-related
    test folder opts in with its own small autouse wrapper."""
    runs = registry.discover("tests.seasons.fake_runs")
    monkeypatch.setattr(registry, "all_runs", lambda: runs)
    registry.all_items.cache_clear()
    yield runs
    registry.all_items.cache_clear()


def _make_game(session, *, season_id, hard_mode=False) -> Game:
    session.add_all([Player(telegram_user_id=u) for u in (WINNER, STARTER)])
    game = Game(
        starter_id=STARTER,
        status=GameStatus.ACTIVE,
        current_stage=PixelStage.STAGE_1,
        title_english="Clannad",
        season_id=season_id,
        hard_mode=hard_mode,
    )
    session.add(game)
    session.flush()
    return game


@pytest.fixture
def active_season(session) -> SeasonSchedule:
    row = SeasonSchedule(
        run_id="demo_1",
        start_at=NOW - timedelta(days=1),
        end_at=NOW + timedelta(days=9),
        status=SeasonStatus.ACTIVE,
        created_by=9,
    )
    session.add(row)
    session.flush()
    return row


@pytest.fixture
def tagged_game(session, active_season) -> Game:
    """A game started by player 2 inside the active demo_1 season."""
    return _make_game(session, season_id=active_season.id)


@pytest.fixture
def bot_tagged_game(session, active_season) -> Game:
    return _make_game(session, season_id=active_season.id, hard_mode=True)


@pytest.fixture
def untagged_game(session) -> Game:
    return _make_game(session, season_id=None)


@pytest.fixture
def end_season(session, active_season):
    def _end() -> None:
        active_season.status = SeasonStatus.ENDED
        session.flush()

    return _end


def _game_in_use(request) -> Game:
    for name in ("tagged_game", "bot_tagged_game", "untagged_game"):
        if name in request.fixturenames:
            return request.getfixturevalue(name)
    raise LookupError("emit_* needs a tagged_game, bot_tagged_game or untagged_game fixture")


@pytest.fixture
def emit_win(session, request):
    game = _game_in_use(request)

    def _emit(*, winner: int, stage: int) -> None:
        events.emit(
            session,
            EventType.GAME_WON,
            events.Involved(actor_id=winner, subject_id=STARTER, game_id=game.id),
            stage=stage,
            hard_mode=False,
        )

    return _emit


@pytest.fixture
def emit_unsolved(session, request):
    game = _game_in_use(request)

    def _emit() -> None:
        events.emit(
            session,
            EventType.GAME_UNSOLVED,
            events.Involved(subject_id=STARTER, game_id=game.id),
        )

    return _emit


@pytest.fixture
def emit_guess(session, request):
    game = _game_in_use(request)

    def _emit(*, player: int) -> None:
        session.add(GameGuess(game_id=game.id, player_id=player, text="x", stage=1, correct=False))
        session.flush()
        events.emit(
            session,
            EventType.GUESS,
            events.Involved(actor_id=player, game_id=game.id),
        )

    return _emit


@pytest.fixture
def buyer(session, request) -> Player:
    """Player 1, created by whichever game fixture the test uses."""
    _game_in_use(request)
    player = session.get(Player, WINNER)
    assert player is not None
    return player


class FixedRng:
    """A stand-in for random.Random with fixed answers (index into choice)."""

    def __init__(self, *, randrange: int = 0, choice: int = 0) -> None:
        self._randrange, self._choice = randrange, choice

    def randrange(self, _stop: int) -> int:
        return self._randrange

    def choice(self, seq):
        return seq[self._choice]


@pytest.fixture
def haunted_season(session) -> SeasonSchedule:
    """An active demo_3 season (haunt 25%, event day Oct 31) around Oct 31 2026."""
    row = SeasonSchedule(
        run_id="demo_3",
        start_at=datetime(2026, 10, 26, tzinfo=UTC),
        end_at=datetime(2026, 11, 9, tzinfo=UTC),
        status=SeasonStatus.ACTIVE,
        created_by=9,
    )
    session.add(row)
    session.flush()
    return row


@pytest.fixture
def haunted_game(session, haunted_season) -> Game:
    return _make_game(session, season_id=haunted_season.id)


@pytest.fixture
def set_group_tz(session):
    def _set(name: str) -> None:
        session.add(BotSettings(id=settings.SETTINGS_ID, quiet_timezone=name))
        session.flush()

    return _set
