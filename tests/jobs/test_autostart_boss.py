"""The Gashadokuro boss game: Nani Pix starts it herself once the season's
100 Candles goal is reached (jobs/timers/autostart.py's maybe_start_boss)."""

from dataclasses import replace
from datetime import UTC, datetime
from typing import cast
from unittest.mock import AsyncMock

import pytest
from sqlalchemy import select
from sqlalchemy.orm import undefer
from telegram.ext import ContextTypes

from nani_pix_bot.db import session_scope
from nani_pix_bot.jobs.timers import autostart as autostart_timers
from nani_pix_bot.jobs.timers import boss as boss_timers
from nani_pix_bot.models.bot_settings import BotSettings
from nani_pix_bot.models.enums import EventType, GameStatus, PixelStage, SeasonStatus
from nani_pix_bot.models.event_log import EventLog
from nani_pix_bot.models.game import Game
from nani_pix_bot.models.player import Player
from nani_pix_bot.models.season import SeasonSchedule
from nani_pix_bot.models.turn_state import TurnState
from nani_pix_bot.services import events, i18n, settings
from nani_pix_bot.services import game as game_service
from nani_pix_bot.services.game import autostart as autostart_service
from nani_pix_bot.services.quiet_hours import QuietHours
from tests.frames import frame_bytes
from tests.jobs.test_autostart import _fake_pick, _make_context
from tests.seasons_fixtures import STARTER, WINNER

BOT_ID = 999


@pytest.fixture(autouse=True)
def _runs(fake_runs):
    return fake_runs


def _pick():
    pick = _fake_pick()
    return replace(pick, screenshot=replace(pick.screenshot, image_bytes_a=frame_bytes(3)))


@pytest.fixture
def gather(monkeypatch: pytest.MonkeyPatch) -> list[object]:
    """gather_pick returns the next queued result (a pick by default)."""
    queued: list[object] = []

    async def fake_gather_pick(search_client, tmdb_client, tenrai_client, gate=None):
        return queued.pop(0) if queued else _pick()

    monkeypatch.setattr(autostart_service, "gather_pick", fake_gather_pick)
    return queued


def _season(session) -> SeasonSchedule:
    season = SeasonSchedule(
        run_id="demo_3",
        start_at=datetime(2026, 10, 1, tzinfo=UTC),
        end_at=datetime(2026, 12, 1, tzinfo=UTC),
        status=SeasonStatus.ACTIVE,
        created_by=9,
    )
    session.add(season)
    session.flush()
    return season


def _world(session_factory, *, games_enabled: bool = True, holder: int | None = WINNER) -> int:
    """An active demo_3 season (goal 10), the turn held by `holder`;
    autostart is off (the boss ignores it). Returns the season id."""
    with session_scope(session_factory) as session:
        session.add_all([Player(telegram_user_id=WINNER), Player(telegram_user_id=STARTER)])
        session.add(TurnState(id=1, next_starter_id=holder))
        session.add(BotSettings(id=1, games_enabled=games_enabled, autostart_enabled=False))
        return _season(session).id


def _settle(session_factory, season_id: int) -> None:
    with session_scope(session_factory) as session:
        events.emit(
            session,
            EventType.GOAL_REACHED,
            events.Involved(actor_id=None),
            season_id=season_id,
            run_id="demo_3",
        )


def _boss(session_factory) -> Game | None:
    with session_factory() as session:
        stmt = select(Game).where(Game.boss_game.is_(True)).options(undefer(Game.original_image))
        return session.scalars(stmt).first()


async def _game_ended(context, session_factory, winner_id: int | None = WINNER) -> None:
    await autostart_timers.maybe_overthrow(
        cast(ContextTypes.DEFAULT_TYPE, context),
        session_factory,
        winner_id=winner_id,
        winner_name="frieren" if winner_id else None,
    )


def _boss_jobs(context) -> list:
    return [
        c
        for c in context.job_queue.run_once.call_args_list
        if c.kwargs.get("name") == boss_timers.BOSS_START_JOB_NAME
    ]


def _context(session_factory) -> ContextTypes.DEFAULT_TYPE:
    context = _make_context(session_factory, bot_id=BOT_ID)
    context.bot.send_photo = AsyncMock(return_value=AsyncMock(message_id=42))
    return cast(ContextTypes.DEFAULT_TYPE, context)


def _sent(context: ContextTypes.DEFAULT_TYPE) -> AsyncMock:
    return cast(AsyncMock, context.bot.send_photo)


async def test_boss_starts_after_the_game_that_lit_the_last_candle(
    session_factory, gather, log_records
) -> None:
    season_id = _world(session_factory)
    with session_scope(session_factory) as session:
        for _ in range(10):
            game = Game(
                starter_id=STARTER,
                status=GameStatus.WON,
                winner_id=WINNER,
                current_stage=PixelStage.STAGE_1,
                title_english="Clannad",
                season_id=season_id,
            )
            session.add(game)
            session.flush()
        events.emit(
            session,
            EventType.GAME_WON,
            events.Involved(actor_id=WINNER, subject_id=STARTER, game_id=game.id),
            stage=1,
            hard_mode=False,
        )
    context = _context(session_factory)

    await _game_ended(context, session_factory)

    boss = _boss(session_factory)
    assert boss is not None
    assert boss.status == GameStatus.ACTIVE
    assert boss.hard_mode is False
    assert boss.current_stage == game_service.STAGE_ORDER[0]
    assert len(game_service.STAGE_ORDER) == 5
    assert boss.starter_id == BOT_ID
    assert boss.original_image == frame_bytes(3)
    caption = _sent(context).call_args.kwargs["caption"]
    assert caption.startswith(i18n.t("dm_start.boss_game_started_caption", "en", xp=2, coins=6))
    started = [r for r in log_records if "claimed by bot autostart (boss)" in r.message]
    assert started[0].level == "INFO"


async def test_boss_start_skips_the_overthrow_roll(
    session_factory, gather, monkeypatch: pytest.MonkeyPatch
) -> None:
    _settle(session_factory, _world(session_factory))
    roll = pytest.fail
    monkeypatch.setattr(autostart_service, "roll_overthrow", lambda: roll("rolled"))

    await _game_ended(_context(session_factory), session_factory)

    assert _boss(session_factory) is not None


async def test_boss_starts_with_autostart_disabled(session_factory, gather) -> None:
    _settle(session_factory, _world(session_factory))
    with session_factory() as session:
        assert settings.get_autostart_enabled(session) is False
    context = _context(session_factory)

    started = await autostart_timers.maybe_start_boss(context, session_factory)

    assert started is True
    assert _boss(session_factory) is not None
    _sent(context).assert_awaited_once()


async def test_no_boss_when_games_are_disabled(session_factory, gather, log_records) -> None:
    _settle(session_factory, _world(session_factory, games_enabled=False))
    context = _context(session_factory)

    assert await autostart_timers.maybe_start_boss(context, session_factory) is False

    assert _boss(session_factory) is None
    line = next(r for r in log_records if "boss game due but" in r.message)
    assert line.level == "INFO"
    assert line.extra["reason"] == "new games are disabled"


async def test_boss_held_until_quiet_hours_end(
    session_factory, gather, quiet_now: QuietHours, log_records
) -> None:
    _settle(session_factory, _world(session_factory))
    with session_scope(session_factory) as session:
        settings.set_quiet_hours(session, quiet_now)
    context = _context(session_factory)

    assert await autostart_timers.maybe_start_boss(context, session_factory) is True

    assert _boss(session_factory) is None
    (job,) = _boss_jobs(context)
    assert job.kwargs["when"] > 60 * 60  # the window ends ~2h from now
    assert any("held until quiet hours end" in r.message and r.level == "INFO" for r in log_records)


async def test_failed_boss_pick_retries_in_an_hour(session_factory, gather, log_records) -> None:
    _settle(session_factory, _world(session_factory))
    gather.append(None)
    context = _context(session_factory)

    assert await autostart_timers.maybe_start_boss(context, session_factory) is True

    assert _boss(session_factory) is None
    (job,) = _boss_jobs(context)
    delay = game_service.AUTOSTART_RETRY_DELAY.total_seconds()
    assert delay - 5 < job.kwargs["when"] <= delay
    assert job.args[0] is boss_timers.boss_start_job_callback
    assert any("pick failed" in r.message and r.level == "INFO" for r in log_records)


async def test_boss_start_aborts_when_a_human_game_started(
    session_factory, monkeypatch: pytest.MonkeyPatch, log_records
) -> None:
    _settle(session_factory, _world(session_factory))

    async def slow_gather_pick(search_client, tmdb_client, tenrai_client, gate=None):
        with session_scope(session_factory) as session:  # a human starts mid-pick
            game_service.create_setup_game(session, starter_id=WINNER)
        return _pick()

    monkeypatch.setattr(autostart_service, "gather_pick", slow_gather_pick)
    context = _context(session_factory)

    assert await autostart_timers.maybe_start_boss(context, session_factory) is False

    assert _boss(session_factory) is None
    _sent(context).assert_not_awaited()
    line = next(r for r in log_records if "aborted" in r.message)
    assert line.level == "WARNING"


async def test_a_human_game_holds_the_boss(session_factory, gather, log_records) -> None:
    _settle(session_factory, _world(session_factory))
    with session_scope(session_factory) as session:
        game_service.create_setup_game(session, starter_id=WINNER)

    assert await autostart_timers.maybe_start_boss(_context(session_factory), session_factory) is (
        False
    )

    assert _boss(session_factory) is None
    line = next(r for r in log_records if "boss game due but" in r.message)
    assert line.level == "INFO"


async def test_a_stopped_boss_starts_again(session_factory, gather) -> None:
    _settle(session_factory, _world(session_factory, holder=None))
    context = _context(session_factory)
    await autostart_timers.maybe_start_boss(context, session_factory)
    first = _boss(session_factory)
    assert first is not None
    with session_scope(session_factory) as session:  # as /stop does
        session.delete(session.get(Game, first.id))
        turn_state = game_service.set_next_starter(session, None, reason="boss stopped")

    autostart_timers.schedule_idle_autostart(context.job_queue, turn_state)
    (job,) = _boss_jobs(context)
    assert job.kwargs["when"] == 0
    context.job = None
    await boss_timers.boss_start_job_callback(context)

    second = _boss(session_factory)
    assert second is not None
    assert second.status == GameStatus.ACTIVE
    # SQLite may reuse the deleted id, so count the stage-1 posts instead.
    assert _sent(context).await_count == 2


async def test_winner_keeps_credit_when_the_boss_takes_the_turn(session_factory, gather) -> None:
    season_id = _world(session_factory)
    with session_scope(session_factory) as session:
        won = Game(
            starter_id=STARTER,
            status=GameStatus.WON,
            winner_id=WINNER,
            current_stage=PixelStage.STAGE_1,
            title_english="Clannad",
            season_id=season_id,
        )
        session.add(won)
        session.flush()
        won_id = won.id
        wins_before = session.get_one(Player, WINNER).wins
    _settle(session_factory, season_id)

    await _game_ended(_context(session_factory), session_factory)

    with session_factory() as session:
        game = session.get(Game, won_id)
        assert game.status == GameStatus.WON
        assert game.winner_id == WINNER
        assert session.get_one(Player, WINNER).wins == wins_before
        overthrown = select(EventLog).where(EventLog.event_type == EventType.OVERTHROWN)
        assert list(session.scalars(overthrown)) == []
        turn_state = game_service.get_turn_state(session)
        assert turn_state is not None
        assert turn_state.next_starter_id is None
    assert _boss(session_factory) is not None


async def test_no_boss_due_leaves_the_overthrow_alone(session_factory, gather) -> None:
    _world(session_factory)

    assert await autostart_timers.maybe_start_boss(_context(session_factory), session_factory) is (
        False
    )
    assert _boss(session_factory) is None
