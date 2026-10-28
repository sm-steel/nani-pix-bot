import pytest
from sqlalchemy import select

from nani_pix_bot.models.announcement import AnnouncementOutbox
from nani_pix_bot.models.enums import EventType, GameStatus, OutboxKind, SeasonStatus
from nani_pix_bot.models.game import Game
from nani_pix_bot.models.player import Player
from nani_pix_bot.services import events
from nani_pix_bot.services.seasons.group_goal import boss_due, boss_game, mark_boss

STARTER = 2


def _outbox(session, kind):
    stmt = select(AnnouncementOutbox).where(AnnouncementOutbox.kind == kind)
    return list(session.scalars(stmt.order_by(AnnouncementOutbox.id)))


def _setup(session) -> Game:
    if session.get(Player, STARTER) is None:
        session.add(Player(telegram_user_id=STARTER))
    game = Game(starter_id=STARTER, status=GameStatus.SETUP, title_english="Clannad")
    session.add(game)
    session.flush()
    return game


@pytest.fixture
def settled_yokai(session, yokai_season):
    """The yokai season with its candle goal already reached."""
    events.emit(
        session,
        EventType.GOAL_REACHED,
        events.Involved(actor_id=None),
        season_id=yokai_season.id,
        run_id="demo_3",
    )
    return yokai_season


@pytest.fixture
def setup_game(session) -> Game:
    return _setup(session)


@pytest.fixture
def second_setup_game(session) -> Game:
    return _setup(session)


def test_boss_due_after_the_goal_until_a_boss_exists(session, settled_yokai, setup_game) -> None:
    assert boss_due(session)
    mark_boss(session, setup_game)
    assert setup_game.boss_game is True
    assert setup_game.season_id == settled_yokai.id
    assert boss_game(session, settled_yokai.id) is setup_game
    assert not boss_due(session)


def test_no_boss_due_before_the_goal(session, yokai_season) -> None:
    assert not boss_due(session)
    assert boss_game(session, yokai_season.id) is None


def test_only_one_boss_per_season(session, settled_yokai, setup_game, second_setup_game) -> None:
    mark_boss(session, setup_game)
    mark_boss(session, second_setup_game)
    assert second_setup_game.boss_game is False
    assert len(_outbox(session, OutboxKind.GOAL_BOSS)) == 1


def test_a_stopped_boss_is_due_again(session, settled_yokai, setup_game) -> None:
    mark_boss(session, setup_game)
    session.delete(setup_game)  # as /stop does
    session.flush()
    assert boss_due(session)


def test_a_finished_boss_is_not_due_again(
    session, settled_yokai, setup_game, finished_game
) -> None:
    finished_game(GameStatus.WON, boss_game=True)
    assert not boss_due(session)


def test_no_boss_due_outside_an_active_season(session, settled_yokai) -> None:
    settled_yokai.status = SeasonStatus.CLOSING
    session.flush()
    assert not boss_due(session)


def test_a_run_without_group_goal_is_never_due(session, active_season) -> None:
    events.emit(
        session,
        EventType.GOAL_REACHED,
        events.Involved(actor_id=None),
        season_id=active_season.id,
        run_id="demo_1",
    )
    assert not boss_due(session)


def test_mark_boss_queues_the_post(session, settled_yokai, setup_game, log_records) -> None:
    mark_boss(session, setup_game)
    payload = _outbox(session, OutboxKind.GOAL_BOSS)[0].payload
    assert payload == {"season_id": settled_yokai.id, "game_id": setup_game.id}
    line = next(r for r in log_records if "Gashadokuro rises" in r.message)
    assert line.level == "INFO"
    assert line.extra["season_id"] == settled_yokai.id
    assert line.extra["run_id"] == "demo_3"


def test_mark_boss_when_not_due_leaves_the_game_alone(session, yokai_season, setup_game) -> None:
    mark_boss(session, setup_game)
    assert setup_game.boss_game is False
    assert _outbox(session, OutboxKind.GOAL_BOSS) == []
