from dataclasses import replace

import pytest
from sqlalchemy import func, select

from nani_pix_bot.models.announcement import AnnouncementOutbox
from nani_pix_bot.models.currency_transfer import CurrencyTransfer
from nani_pix_bot.models.enums import (
    Currency,
    CurrencyReason,
    EventType,
    GameStatus,
    OutboxKind,
    SeasonStatus,
)
from nani_pix_bot.models.event_log import EventLog
from nani_pix_bot.models.game import Game
from nani_pix_bot.seasons import registry
from nani_pix_bot.services import events
from nani_pix_bot.services.cosmetics import inventory
from nani_pix_bot.services.economy import wallet
from nani_pix_bot.services.seasons import consumer, season_history
from nani_pix_bot.services.seasons.group_goal import contributors, is_settled, settle
from tests.seasons.fake_runs import demo_3

ITEM = "demo_3/name_flair.candle"
LATE = 77


@pytest.fixture
def finish(session, finished_game):
    """k plain candle games (host 2), each finished and its event emitted, so
    the real consumer runs."""

    def _finish(n=1, *, guessers=(3,), status=GameStatus.WON, **fields) -> Game:
        game = None
        for _ in range(n):
            game = finished_game(status, guessers=guessers, **fields)
            kind = EventType.GAME_WON if status == GameStatus.WON else EventType.GAME_UNSOLVED
            events.emit(session, kind, events.Involved(game_id=game.id, subject_id=2))
        assert game is not None
        return game

    return _finish


@pytest.fixture
def run_with_every_4(monkeypatch):
    run = demo_3.RUN
    assert run.group_goal is not None
    goal = replace(run.group_goal, milestone_every=4)
    runs = {**registry.all_runs(), "demo_3": replace(run, group_goal=goal)}
    monkeypatch.setattr(registry, "all_runs", lambda: runs)
    registry.all_items.cache_clear()
    yield
    registry.all_items.cache_clear()


def _outbox(session, kind):
    stmt = select(AnnouncementOutbox).where(AnnouncementOutbox.kind == kind)
    return list(session.scalars(stmt.order_by(AnnouncementOutbox.id)))


def _milestones(session):
    return [row.payload["milestone"] for row in _outbox(session, OutboxKind.GOAL_MILESTONE)]


def _reached(session):
    stmt = select(EventLog).where(EventLog.event_type == EventType.GOAL_REACHED)
    return list(session.scalars(stmt))


def _run():
    return demo_3.RUN


def _goal_rows(session):
    stmt = select(func.count(CurrencyTransfer.id)).where(
        CurrencyTransfer.reason == CurrencyReason.SEASON_GOAL
    )
    return session.scalar(stmt)


def test_milestone_queued_once_when_crossed(session, yokai_season, finish, run_with_every_4):
    finish(n=4)
    finish(n=1)
    assert _milestones(session) == [4]


def test_each_milestone_is_queued_in_turn(session, yokai_season, finish, run_with_every_4):
    finish(n=8)
    assert _milestones(session) == [4, 8]


def test_settle_pays_every_contributor_coins_item_and_event(session, yokai_season, finish):
    last = finish(n=10)
    paid = contributors(session, yokai_season.id)
    assert paid == {2, 3}
    for pid in paid:
        assert wallet.balance(session, pid, season_id=yokai_season.id) >= 20
        assert inventory.owns(session, pid, ITEM)
    reached = _reached(session)
    assert {e.actor_id for e in reached} == paid
    assert all(e.data["crossing_game_id"] == last.id for e in reached)
    assert _outbox(session, OutboxKind.GOAL_REACHED)[0].payload == {
        "season_id": yokai_season.id,
        "players": len(reached),
        "candles": 10,
    }


def test_settle_pays_once_across_repeated_and_later_game_ends(session, yokai_season, finish):
    finish(n=10)
    paid = _goal_rows(session)
    finish(n=1)
    assert settle(session, yokai_season, _run(), None) is False
    assert len(_outbox(session, OutboxKind.GOAL_REACHED)) == 1
    assert _goal_rows(session) == paid == len(contributors(session, yokai_season.id))


def test_setwinner_refinish_does_not_pay_again(session, yokai_season, finish):
    finish(n=9)
    last = finish(n=1, status=GameStatus.UNSOLVED)
    assert len(_outbox(session, OutboxKind.GOAL_REACHED)) == 1
    last.status, last.winner_id = GameStatus.WON, 3
    events.emit(session, EventType.GAME_WON, events.Involved(game_id=last.id, actor_id=3))
    assert len(_outbox(session, OutboxKind.GOAL_REACHED)) == 1
    assert len(_reached(session)) == 2


def test_late_players_are_not_paid(session, yokai_season, finish):
    finish(n=10)
    finish(n=1, guessers=(LATE,))
    assert wallet.balance(session, LATE, season_id=yokai_season.id) == 0
    assert not inventory.owns(session, LATE, ITEM)


def test_boss_game_end_snuffs_nothing_and_settles_nothing_new(session, yokai_season, finish):
    finish(n=9)
    finish(n=1, boss_game=True)
    assert _reached(session) == []
    assert _outbox(session, OutboxKind.GOAL_REACHED) == []


def test_payout_on_the_last_game_of_a_closing_season_is_converted(session, yokai_season, finish):
    finish(n=9)
    yokai_season.status = SeasonStatus.CLOSING
    finish(n=1)
    assert yokai_season.status == SeasonStatus.ENDED
    stmt = select(func.sum(CurrencyTransfer.amount)).where(
        CurrencyTransfer.reason == CurrencyReason.SEASON_CONVERSION,
        CurrencyTransfer.currency == Currency.SEASON,
    )
    assert session.scalar(stmt) >= 20 * len(contributors(session, yokai_season.id))


def test_reached_with_no_contributors_still_marks_settled(
    session, yokai_season, finish, log_records
):
    finish(n=10, guessers=(), hard_mode=True)
    reached = _reached(session)
    assert [e.actor_id for e in reached] == [None]
    assert is_settled(session, yokai_season.id)
    assert any(r.level == "WARNING" and "no contributors" in r.message for r in log_records)


def test_goal_reached_is_in_the_season_history(session, yokai_season, finish):
    finish(n=10)
    history = season_history.for_player(session, yokai_season, 3)
    assert len(history.mine(EventType.GOAL_REACHED)) == 1


def test_payout_logs_structured_fields(session, yokai_season, finish, log_records):
    finish(n=10)
    line = next(r for r in log_records if "goal reached" in r.message and r.level == "INFO")
    assert line.extra["season_id"] == yokai_season.id
    assert line.extra["players"] == 2
    assert line.extra["candles"] == 10


def test_untagged_or_goalless_run_is_a_no_op(session, active_season, tagged_game, emit_win):
    emit_win(winner=1, stage=1)
    assert _reached(session) == []
    assert _milestones(session) == []


def test_a_broken_goal_does_not_break_the_event(session, yokai_season, finish, monkeypatch):
    def boom(*_args):
        raise RuntimeError("boom")

    monkeypatch.setattr(consumer.group_goal, "on_game_ended", boom)
    finish(n=1)
    assert _reached(session) == []
