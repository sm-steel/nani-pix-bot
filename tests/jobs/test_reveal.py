import asyncio
import threading
from concurrent.futures import ThreadPoolExecutor
from concurrent.futures.process import BrokenProcessPool
from unittest.mock import AsyncMock, MagicMock

import pytest

from nani_pix_bot.db import session_scope
from nani_pix_bot.jobs import reveal
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
    application.create_task = lambda coro, **_: asyncio.ensure_future(coro)
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


async def _settle() -> None:
    for _ in range(20):
        await asyncio.sleep(0.01)


async def test_pregeneration_fills_cache_and_db(session_factory, monkeypatch) -> None:
    monkeypatch.setattr(pipeline, "pregenerate", lambda *args: PREGEN)
    game_id = _game(session_factory)
    _reserve(session_factory, game_id)
    application = _application(session_factory)
    reveal.start_pregeneration(application, game_id)
    cache = application.bot_data["reveal_cache"]
    assert await asyncio.wait_for(cache.future, 2) == PREGEN
    await _settle()
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
    reveal.start_pregeneration(_application(session_factory), game_id)
    await _settle()
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
    await _settle()
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

    monkeypatch.setattr(pipeline, "finish", _broken)
    application = _application(session_factory)
    old_executor = application.bot_data["reveal_executor"]
    application.bot_data["reveal_cache"].ready(5, PREGEN)
    assert await reveal.render_reveal(application, 5, b"png", None, None) is None
    assert application.bot_data["reveal_executor"] is not old_executor
    reveal.stop_worker(application.bot_data)


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
    await reveal.reload_on_startup(_application(session_factory))
    await _settle()
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


async def test_pregeneration_is_a_quiet_noop_without_a_worker(
    session_factory, monkeypatch, log_records
) -> None:
    pregenerate = MagicMock(side_effect=AssertionError("must not render"))
    monkeypatch.setattr(pipeline, "pregenerate", pregenerate)
    game_id = _game(session_factory)
    _reserve(session_factory, game_id)
    application = MagicMock()  # bot_data is a MagicMock: no real worker or cache
    reveal.start_pregeneration(application, game_id)
    await _settle()
    pregenerate.assert_not_called()
    assert not any(line.level in ("WARNING", "ERROR") for line in log_records)


async def test_render_without_a_worker_falls_back_to_the_photo(log_records) -> None:
    application = MagicMock()
    assert await reveal.render_reveal(application, 5, b"png", None, None) is None
    assert any("photo" in line.message for line in log_records if line.level == "INFO")
