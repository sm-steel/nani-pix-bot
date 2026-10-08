import asyncio
import threading
from concurrent.futures import ThreadPoolExecutor
from concurrent.futures.process import BrokenProcessPool
from typing import cast
from unittest.mock import AsyncMock, MagicMock

import pytest

from nani_pix_bot.db import session_scope
from nani_pix_bot.jobs import reveal, reveal_pregen, reveal_worker
from nani_pix_bot.models.enums import GameStatus, PixelStage, RevealEffect, RevealStatus
from nani_pix_bot.models.game import Game
from nani_pix_bot.models.player import Player
from nani_pix_bot.services.reveal import pipeline, store

PREGEN = pipeline.Pregen(part1_ts=b"\x47ts", join_offset=1.0, render_ms=12)


def _application(session_factory) -> MagicMock:
    application = MagicMock()
    application.bot_data = {
        "session_factory": session_factory,
        "reveal_executor": ThreadPoolExecutor(1),
        "reveal_cache": reveal.RevealCache(),
    }
    application.bot.get_user_profile_photos = AsyncMock(return_value=MagicMock(photos=[]))
    application.tasks = []

    def _create_task(coro, **_):
        application.tasks.append(asyncio.ensure_future(coro))
        return application.tasks[-1]

    application.create_task = _create_task
    return application


def _game(session_factory, status: GameStatus = GameStatus.ACTIVE) -> int:
    with session_factory() as session:
        if session.get(Player, 1) is None:
            session.add(Player(telegram_user_id=1))
        game = Game(
            starter_id=1,
            original_image=b"png-bytes",
            status=status,
            current_stage=PixelStage.STAGE_1,
            wrong_guess_count=0,
            anilist_id=99,
            title_romaji="Sousou no Frieren",
            synonyms=[],
        )
        session.add(game)
        session.commit()
        return game.id


def _reserve(session_factory, game_id: int, effect: RevealEffect = RevealEffect.IRIS) -> None:
    with session_scope(session_factory) as session:
        store.reserve(session, game_id, effect, None)


def _slot(session_factory):
    with session_scope(session_factory) as session:
        row = store.load(session)
        return None if row is None else (row.game_id, row.status)


async def _settle(application) -> None:
    """Awaits the background tasks the code under test spawned (no wall-clock sleeping)."""
    await asyncio.gather(*application.tasks, *reveal_pregen._startup_tasks, return_exceptions=True)


async def test_pregeneration_fills_cache_and_db(session_factory, monkeypatch) -> None:
    monkeypatch.setattr(pipeline, "pregenerate", lambda *args: PREGEN)
    game_id = _game(session_factory)
    _reserve(session_factory, game_id)
    application = _application(session_factory)
    reveal.start_pregeneration(application, game_id)
    cache = application.bot_data["reveal_cache"]
    assert await asyncio.wait_for(cache.future, 2) == PREGEN
    await _settle(application)
    assert (cache.game_id, cache.pregen) == (game_id, PREGEN)
    assert _slot(session_factory) == (game_id, RevealStatus.READY)


async def test_failed_pregeneration_marks_the_slot_failed(
    session_factory, monkeypatch, log_records
) -> None:
    def _boom(*args):
        raise RuntimeError("ffmpeg exploded")

    monkeypatch.setattr(pipeline, "pregenerate", _boom)
    game_id = _game(session_factory)
    _reserve(session_factory, game_id)
    application = _application(session_factory)
    reveal.start_pregeneration(application, game_id)
    await _settle(application)
    assert _slot(session_factory) == (game_id, RevealStatus.FAILED)
    assert any(line.level == "ERROR" for line in log_records)


async def test_stale_render_is_discarded(session_factory, monkeypatch) -> None:
    gate = threading.Event()

    def _slow(*args):
        gate.wait(2)
        return PREGEN

    monkeypatch.setattr(pipeline, "pregenerate", _slow)
    application = _application(session_factory)
    old = _game(session_factory)
    _reserve(session_factory, old)
    reveal.start_pregeneration(application, old)
    new = _game(session_factory)
    _reserve(session_factory, new, RevealEffect.GLITCH)
    application.bot_data["reveal_cache"].pending(new, asyncio.get_running_loop().create_future())
    gate.set()
    await _settle(application)
    assert _slot(session_factory) == (new, RevealStatus.PENDING)
    cache = application.bot_data["reveal_cache"]
    assert (cache.game_id, cache.pregen) == (new, None)


async def test_reveal_waits_then_falls_back(session_factory, monkeypatch, log_records) -> None:
    monkeypatch.setattr(reveal, "WAIT_SECONDS", 0.05)
    game_id = _game(session_factory)
    _reserve(session_factory, game_id)
    application = _application(session_factory)
    application.bot_data["reveal_cache"].pending(
        game_id, asyncio.get_running_loop().create_future()
    )
    assert await reveal.render_reveal(application, game_id, b"png", None, None) is None
    assert any(line.level == "WARNING" for line in log_records)
    assert any("photo" in line.message for line in log_records if line.level == "INFO")


async def test_ready_cache_renders_with_badge(session_factory, monkeypatch) -> None:
    finish = MagicMock(return_value=b"mp4")
    monkeypatch.setattr(pipeline, "finish", finish)
    application = _application(session_factory)
    application.bot_data["reveal_cache"].ready(5, PREGEN)
    assert await reveal.render_reveal(application, 5, b"png", 9, "@w") == b"mp4"
    pregen, clear, badge = finish.call_args.args
    assert (pregen, clear, badge) == (PREGEN, b"png", pipeline.Badge(avatar=None, handle="@w"))


async def test_broken_worker_falls_back_and_is_replaced(session_factory, monkeypatch) -> None:
    def _broken(*args):
        raise BrokenProcessPool("worker died")

    replacements: list[ThreadPoolExecutor] = []

    def _fake_pool(**_kwargs) -> ThreadPoolExecutor:
        replacements.append(ThreadPoolExecutor(1))  # stands in for the spawn process pool
        return replacements[-1]

    monkeypatch.setattr(pipeline, "finish", _broken)
    monkeypatch.setattr(reveal_worker, "ProcessPoolExecutor", _fake_pool)
    application = _application(session_factory)
    old_executor = application.bot_data["reveal_executor"]
    application.bot_data["reveal_cache"].ready(5, PREGEN)
    assert await reveal.render_reveal(application, 5, b"png", None, None) is None
    assert replacements == [application.bot_data["reveal_executor"]]
    assert application.bot_data["reveal_executor"] is not old_executor
    old_executor.shutdown()
    replacements[0].shutdown()


async def test_reload_puts_a_ready_slot_in_the_cache(session_factory, monkeypatch) -> None:
    submitted = MagicMock(side_effect=AssertionError("must not re-render a ready slot"))
    monkeypatch.setattr(pipeline, "pregenerate", submitted)
    game_id = _game(session_factory)
    _reserve(session_factory, game_id)
    with session_scope(session_factory) as session:
        store.mark_ready(session, game_id, b"\x47ts", 1.0)
    application = _application(session_factory)
    await reveal.reload_on_startup(application)
    cache = application.bot_data["reveal_cache"]
    assert cache.game_id == game_id
    assert cache.pregen == pipeline.Pregen(part1_ts=b"\x47ts", join_offset=1.0, render_ms=0)


async def test_reload_rerenders_a_pending_slot_of_an_active_game(
    session_factory, monkeypatch
) -> None:
    monkeypatch.setattr(pipeline, "pregenerate", lambda *args: PREGEN)
    game_id = _game(session_factory)
    _reserve(session_factory, game_id)
    application = _application(session_factory)
    await reveal.reload_on_startup(application)
    await _settle(application)
    assert _slot(session_factory) == (game_id, RevealStatus.READY)


@pytest.mark.parametrize("status", [GameStatus.WON, GameStatus.UNSOLVED])
async def test_reload_clears_the_slot_of_an_ended_game(session_factory, status) -> None:
    game_id = _game(session_factory, status)
    _reserve(session_factory, game_id)
    await reveal.reload_on_startup(_application(session_factory))
    assert _slot(session_factory) is None


async def test_reload_clears_the_slot_of_a_deleted_game(session_factory) -> None:
    _reserve(session_factory, 4242)
    await reveal.reload_on_startup(_application(session_factory))
    assert _slot(session_factory) is None


def _hard_mode_game(session_factory) -> int:
    with session_factory() as session:
        if session.get(Player, 1) is None:
            session.add(Player(telegram_user_id=1))
        game = Game(
            starter_id=1,
            status=GameStatus.ACTIVE,
            hard_mode=True,
            hard_mode_turn=1,
            hard_mode_image_a=b"image-a",
            hard_mode_image_b=b"image-b",
            wrong_guess_count=0,
            anilist_id=99,
            title_romaji="Sousou no Frieren",
            synonyms=[],
        )
        session.add(game)
        session.commit()
        return game.id


def _slot_choice(session_factory) -> tuple[int, RevealStatus, str | None] | None:
    with session_scope(session_factory) as session:
        row = store.load(session)
        return None if row is None else (row.game_id, row.status, row.image_choice)


async def test_reload_backfills_a_game_that_was_already_running(
    session_factory, monkeypatch, log_records
) -> None:
    """The game spanning the deploy that added the reveal has no slot at all:
    it gets one at startup and its pre-render runs, so its ending animates."""
    rendered = MagicMock(return_value=PREGEN)
    monkeypatch.setattr(pipeline, "pregenerate", rendered)
    game_id = _game(session_factory)
    application = _application(session_factory)
    await reveal.reload_on_startup(application)
    await _settle(application)
    assert _slot_choice(session_factory) == (game_id, RevealStatus.READY, None)
    assert rendered.call_args.args[0] == b"png-bytes"
    assert application.bot_data["reveal_cache"].pregen == PREGEN
    backfill = [r for r in log_records if "running game" in r.message]
    assert [(r.level, r.extra.get("game_id")) for r in backfill] == [("INFO", game_id)]


async def test_reload_backfills_a_hard_mode_game_with_an_image_choice(
    session_factory, monkeypatch
) -> None:
    monkeypatch.setattr(pipeline, "pregenerate", MagicMock(return_value=PREGEN))
    game_id = _hard_mode_game(session_factory)
    application = _application(session_factory)
    await reveal.reload_on_startup(application)
    await _settle(application)
    slot = _slot_choice(session_factory)
    assert slot is not None
    assert slot[:2] == (game_id, RevealStatus.READY)
    assert slot[2] in {"a", "b"}


async def test_reload_replaces_a_stale_slot_with_the_running_game(
    session_factory, monkeypatch
) -> None:
    """A ready slot left over from an ended game is neither reloaded into the
    cache nor kept: the running game takes the slot."""
    monkeypatch.setattr(pipeline, "pregenerate", MagicMock(return_value=PREGEN))
    ended = _game(session_factory, GameStatus.WON)
    _reserve(session_factory, ended)
    with session_scope(session_factory) as session:
        store.mark_ready(session, ended, b"\x47old", 1.0)
    running = _game(session_factory)
    application = _application(session_factory)
    await reveal.reload_on_startup(application)
    await _settle(application)
    assert _slot_choice(session_factory) == (running, RevealStatus.READY, None)
    assert application.bot_data["reveal_cache"].game_id == running


async def test_reload_does_not_cache_a_ready_slot_of_an_ended_game(session_factory) -> None:
    ended = _game(session_factory, GameStatus.UNSOLVED)
    _reserve(session_factory, ended)
    with session_scope(session_factory) as session:
        store.mark_ready(session, ended, b"\x47old", 1.0)
    application = _application(session_factory)
    await reveal.reload_on_startup(application)
    assert _slot(session_factory) is None
    assert application.bot_data["reveal_cache"].pregen is None


@pytest.mark.parametrize("status", [GameStatus.VOTING, GameStatus.SETUP])
async def test_reload_does_not_backfill_a_vote_or_a_setup(session_factory, status) -> None:
    """A hard-mode vote already posted its reveal when it opened, and a game
    still in setup gets its slot when it's activated."""
    _game(session_factory, status)
    application = _application(session_factory)
    await reveal.reload_on_startup(application)
    await _settle(application)
    assert _slot(session_factory) is None


async def test_reload_with_no_slot_and_no_game_does_nothing(session_factory) -> None:
    application = _application(session_factory)
    await reveal.reload_on_startup(application)
    assert _slot(session_factory) is None
    assert application.tasks == []


async def test_pregeneration_is_a_quiet_noop_without_a_worker(
    session_factory, monkeypatch, log_records
) -> None:
    pregenerate = MagicMock(side_effect=AssertionError("must not render"))
    monkeypatch.setattr(pipeline, "pregenerate", pregenerate)
    game_id = _game(session_factory)
    _reserve(session_factory, game_id)
    application = MagicMock()  # bot_data is a MagicMock: no real worker or cache
    reveal.start_pregeneration(application, game_id)
    pregenerate.assert_not_called()
    assert not any(line.level in ("WARNING", "ERROR") for line in log_records)


async def test_render_without_a_worker_falls_back_to_the_photo(log_records) -> None:
    application = MagicMock()
    assert await reveal.render_reveal(application, 5, b"png", None, None) is None
    assert any("photo" in line.message for line in log_records if line.level == "INFO")


async def test_future_settles_when_mark_failed_raises(session_factory, monkeypatch) -> None:
    def _boom(*args):
        raise RuntimeError("ffmpeg exploded")

    def _db_down(*args):
        raise RuntimeError("db down")

    monkeypatch.setattr(pipeline, "pregenerate", _boom)
    monkeypatch.setattr(store, "mark_failed", _db_down)
    game_id = _game(session_factory)
    _reserve(session_factory, game_id)
    application = _application(session_factory)
    reveal.start_pregeneration(application, game_id)
    future = application.bot_data["reveal_cache"].future
    await asyncio.wait_for(asyncio.wait([future]), 2)
    assert isinstance(future.exception(), RuntimeError)
    application.bot_data["reveal_executor"].shutdown()


async def test_future_settles_when_the_task_is_cancelled(session_factory, monkeypatch) -> None:
    gate, started = threading.Event(), threading.Event()

    def _slow(*args):
        started.set()
        gate.wait(2)
        return PREGEN

    monkeypatch.setattr(pipeline, "pregenerate", _slow)
    game_id = _game(session_factory)
    _reserve(session_factory, game_id)
    application = _application(session_factory)
    tasks: list[asyncio.Task] = []
    application.create_task = lambda coro, **_: tasks.append(asyncio.ensure_future(coro))
    reveal.start_pregeneration(application, game_id)
    assert await asyncio.to_thread(started.wait, 2)  # the task is inside the worker call
    tasks[0].cancel()
    await asyncio.wait(tasks)
    future = application.bot_data["reveal_cache"].future
    assert future.done()
    assert isinstance(future.exception(), RuntimeError)
    gate.set()
    application.bot_data["reveal_executor"].shutdown()


async def test_start_pregeneration_never_raises_on_a_db_failure(
    session_factory, monkeypatch, log_records
) -> None:
    def _boom(*args):
        raise RuntimeError("db down")

    monkeypatch.setattr(reveal_pregen, "_read_inputs", _boom)
    application = _application(session_factory)
    reveal.start_pregeneration(application, 1)
    assert any(line.level == "ERROR" for line in log_records)
    application.bot_data["reveal_executor"].shutdown()


async def test_nothing_to_render_marks_the_slot_failed(session_factory) -> None:
    _reserve(session_factory, 4242)  # no such game
    application = _application(session_factory)
    reveal.start_pregeneration(application, 4242)
    assert _slot(session_factory) == (4242, RevealStatus.FAILED)
    application.bot_data["reveal_executor"].shutdown()


async def test_concurrent_broken_pools_replace_the_executor_once(
    session_factory, monkeypatch
) -> None:
    barrier = threading.Barrier(2)

    def _broken(*args):
        barrier.wait(2)
        raise BrokenProcessPool("worker died")

    replacements: list[ThreadPoolExecutor] = []

    def _start_worker(bot_data) -> None:
        replacements.append(ThreadPoolExecutor(1))
        bot_data["reveal_executor"] = replacements[-1]

    monkeypatch.setattr(reveal_worker, "start_worker", _start_worker)
    bot_data = {"reveal_executor": ThreadPoolExecutor(2)}
    old = bot_data["reveal_executor"]
    results = await asyncio.gather(
        reveal_worker.submit(bot_data, _broken),
        reveal_worker.submit(bot_data, _broken),
        return_exceptions=True,
    )
    assert all(isinstance(r, BrokenProcessPool) for r in results)
    assert len(replacements) == 1
    old.shutdown()
    replacements[0].shutdown()


async def test_avatar_timeout_warns_with_the_winner(
    session_factory, monkeypatch, log_records
) -> None:
    async def _slow_avatar(bot, user_id):
        await asyncio.sleep(1)

    monkeypatch.setattr(reveal, "AVATAR_TIMEOUT", 0.05)
    monkeypatch.setattr(reveal, "fetch_avatar", _slow_avatar)
    monkeypatch.setattr(pipeline, "finish", MagicMock(return_value=b"mp4"))
    application = _application(session_factory)
    application.bot_data["reveal_cache"].ready(5, PREGEN)
    assert await reveal.render_reveal(application, 5, b"png", 9, "@w") == b"mp4"
    warnings = [line for line in log_records if line.level == "WARNING"]
    assert [line.extra["winner_id"] for line in warnings] == [9]
    assert [line.extra["winner"] for line in warnings] == ["9 (@w)"]
    application.bot_data["reveal_executor"].shutdown()


def test_warm_up_failure_is_logged(monkeypatch, log_records) -> None:
    def _boom() -> None:
        raise RuntimeError("import failed")

    monkeypatch.setattr(pipeline, "warm_up", _boom)
    monkeypatch.setattr(reveal_worker, "ProcessPoolExecutor", lambda **_: ThreadPoolExecutor(1))
    bot_data: dict = {}
    reveal.start_worker(bot_data)
    bot_data["reveal_executor"].shutdown(wait=True)
    assert any(line.level == "ERROR" for line in log_records)


async def test_a_slow_render_times_out_and_falls_back(
    session_factory, monkeypatch, log_records
) -> None:
    gate = threading.Event()

    def _slow(*args):
        gate.wait(2)
        return b"mp4"

    monkeypatch.setattr(reveal, "RENDER_TIMEOUT", 0.05)
    monkeypatch.setattr(pipeline, "finish", _slow)
    application = _application(session_factory)
    application.bot_data["reveal_cache"].ready(5, PREGEN)
    assert await reveal.render_reveal(application, 5, b"png", None, None) is None
    assert any(line.level == "WARNING" for line in log_records)
    assert any("photo" in line.message for line in log_records if line.level == "INFO")
    gate.set()
    application.bot_data["reveal_executor"].shutdown()


async def test_the_avatar_fetch_overlaps_the_pre_render_wait(session_factory, monkeypatch) -> None:
    """Each side finishes only once the other has started, so running them one after the other
    would deadlock (and time the test out) instead of passing."""
    avatar_started, pregen_started = asyncio.Event(), asyncio.Event()

    async def _avatar(bot, user_id):
        avatar_started.set()
        await pregen_started.wait()
        return b"avatar"

    async def _find(*args):
        pregen_started.set()
        await avatar_started.wait()
        return PREGEN

    monkeypatch.setattr(reveal, "fetch_avatar", _avatar)
    monkeypatch.setattr(reveal, "_find_pregen", _find)
    finish = MagicMock(return_value=b"mp4")
    monkeypatch.setattr(pipeline, "finish", finish)
    monkeypatch.setattr(reveal, "AVATAR_TIMEOUT", 30.0)  # a serial run must deadlock, not time out
    application = _application(session_factory)
    render = reveal.render_reveal(application, 5, b"png", 9, "@w")
    assert await asyncio.wait_for(render, 1) == b"mp4"
    assert finish.call_args.args[2] == pipeline.Badge(avatar=b"avatar", handle="@w")
    application.bot_data["reveal_executor"].shutdown()


async def test_a_finished_render_logs_its_timing_with_the_game(
    session_factory, monkeypatch, log_records
) -> None:
    monkeypatch.setattr(pipeline, "finish", MagicMock(return_value=b"mp4"))
    application = _application(session_factory)
    application.bot_data["reveal_cache"].ready(5, PREGEN)
    assert await reveal.render_reveal(application, 5, b"png", None, None) == b"mp4"
    [line] = [line for line in log_records if line.message.startswith("reveal rendered")]
    assert line.level == "INFO"
    assert line.extra["game_id"] == 5
    assert isinstance(line.extra["ms"], int)
    assert line.extra["kib"] == 0
    application.bot_data["reveal_executor"].shutdown()


async def test_startup_rerender_does_not_use_the_not_yet_running_application(
    session_factory, monkeypatch
) -> None:
    """application.create_task before the app is running makes PTB warn that the task
    won't be awaited automatically; the startup re-render spawns on the loop itself."""
    monkeypatch.setattr(pipeline, "pregenerate", lambda *args: PREGEN)
    game_id = _game(session_factory)
    _reserve(session_factory, game_id)
    application = _application(session_factory)
    application.create_task = MagicMock(side_effect=AssertionError("PTB would warn"))
    await reveal.reload_on_startup(application)
    await _settle(application)
    assert _slot(session_factory) == (game_id, RevealStatus.READY)
    assert not reveal_pregen._startup_tasks
    application.bot_data["reveal_executor"].shutdown()


async def test_pre_render_start_is_logged(session_factory, monkeypatch, log_records) -> None:
    monkeypatch.setattr(pipeline, "pregenerate", lambda *args: PREGEN)
    game_id = _game(session_factory)
    _reserve(session_factory, game_id)
    application = _application(session_factory)
    reveal.start_pregeneration(application, game_id)
    [line] = [line for line in log_records if line.message.startswith("reveal pre-render started")]
    assert (line.level, line.extra["game_id"], line.extra["effect"]) == ("INFO", game_id, "iris")
    await application.bot_data["reveal_cache"].future
    application.bot_data["reveal_executor"].shutdown()


async def test_a_failed_mark_on_a_foreign_slot_does_not_claim_a_render_was_discarded(
    session_factory, log_records
) -> None:
    _reserve(session_factory, _game(session_factory))
    application = _application(session_factory)
    reveal.start_pregeneration(application, 4242)  # no such game: nothing to render
    assert not any("discarded" in line.message for line in log_records)
    assert any("another game" in line.message for line in log_records if line.level == "INFO")
    application.bot_data["reveal_executor"].shutdown()


def _hanging_avatar(monkeypatch) -> tuple[list[asyncio.Task], asyncio.Event]:
    """Replaces the avatar fetch with one that hangs; returns its captured task and a
    'started' event."""
    tasks: list[asyncio.Task] = []
    started = asyncio.Event()

    async def _avatar(*args):
        tasks.append(cast(asyncio.Task, asyncio.current_task()))
        started.set()
        await asyncio.Event().wait()

    monkeypatch.setattr(reveal, "_avatar", _avatar)
    return tasks, started


async def test_the_avatar_task_is_cancelled_when_finding_the_pregen_raises(
    session_factory, monkeypatch
) -> None:
    tasks, _ = _hanging_avatar(monkeypatch)

    async def _boom(*args):
        await asyncio.sleep(0)  # let the avatar task start
        raise RuntimeError("db down")

    monkeypatch.setattr(reveal, "_find_pregen", _boom)
    application = _application(session_factory)
    with pytest.raises(RuntimeError, match="db down"):
        await reveal.render_reveal(application, 5, b"png", 9, "@w")
    await asyncio.wait_for(asyncio.wait(tasks), 5)
    assert tasks[0].cancelled()
    application.bot_data["reveal_executor"].shutdown()


async def test_the_avatar_task_is_cancelled_when_the_render_is_cancelled(
    session_factory, monkeypatch
) -> None:
    tasks, started = _hanging_avatar(monkeypatch)

    async def _wait_forever(*args):
        await asyncio.Event().wait()

    monkeypatch.setattr(reveal, "_find_pregen", _wait_forever)
    application = _application(session_factory)
    render = asyncio.ensure_future(reveal.render_reveal(application, 5, b"png", 9, "@w"))
    await started.wait()
    render.cancel()
    await asyncio.wait_for(asyncio.wait([render]), 5)
    await asyncio.wait_for(asyncio.wait(tasks), 5)
    assert tasks[0].cancelled()
    application.bot_data["reveal_executor"].shutdown()


async def test_shutdown_cancels_the_pending_startup_rerenders(session_factory, monkeypatch) -> None:
    gate, started = threading.Event(), threading.Event()

    def _slow(*args):
        started.set()
        gate.wait(2)
        return PREGEN

    monkeypatch.setattr(pipeline, "pregenerate", _slow)
    game_id = _game(session_factory)
    _reserve(session_factory, game_id)
    application = _application(session_factory)
    await reveal.reload_on_startup(application)
    assert await asyncio.to_thread(started.wait, 2)
    [task] = reveal_pregen._startup_tasks
    await reveal_pregen.cancel_startup_tasks()
    assert task.cancelled()
    assert not reveal_pregen._startup_tasks
    gate.set()
    application.bot_data["reveal_executor"].shutdown()


async def test_a_scheduling_failure_marks_the_slot_failed(session_factory, monkeypatch) -> None:
    def _no_task(*args, **kwargs):
        raise RuntimeError("loop closed")

    monkeypatch.setattr(pipeline, "pregenerate", lambda *args: PREGEN)
    game_id = _game(session_factory)
    _reserve(session_factory, game_id)
    application = _application(session_factory)
    application.create_task = _no_task
    reveal.start_pregeneration(application, game_id)
    assert _slot(session_factory) == (game_id, RevealStatus.FAILED)
    assert isinstance(application.bot_data["reveal_cache"].future.exception(), RuntimeError)
    application.bot_data["reveal_executor"].shutdown()


async def test_a_store_write_failure_is_not_reported_as_a_failed_render(
    session_factory, monkeypatch, log_records
) -> None:
    def _db_down(*args):
        raise RuntimeError("db down")

    monkeypatch.setattr(pipeline, "pregenerate", lambda *args: PREGEN)
    monkeypatch.setattr(store, "mark_ready", _db_down)
    game_id = _game(session_factory)
    _reserve(session_factory, game_id)
    application = _application(session_factory)
    reveal.start_pregeneration(application, game_id)
    await _settle(application)
    errors = [line.message for line in log_records if line.level == "ERROR"]
    assert errors == ["couldn't store the pre-rendered reveal"]
    assert _slot(session_factory) == (game_id, RevealStatus.FAILED)
    assert isinstance(application.bot_data["reveal_cache"].future.exception(), RuntimeError)
    application.bot_data["reveal_executor"].shutdown()


async def test_a_failed_mark_with_no_slot_says_so(session_factory, log_records) -> None:
    application = _application(session_factory)
    reveal.start_pregeneration(application, 4242)  # no game, no slot
    lines = [line.message for line in log_records if line.level == "INFO"]
    assert any("no reveal slot any more" in m for m in lines)
    assert not any("another game" in m for m in lines)
    application.bot_data["reveal_executor"].shutdown()
