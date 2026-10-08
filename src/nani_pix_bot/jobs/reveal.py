"""Telegram-side lifecycle of the animated reveal (#295): the
fire-and-forget pre-render at game start, the reveal-time render, and the
startup reload from the DB slot. The one-worker process pool and the
one-entry in-memory cache of the pre-rendered effect part live in
`reveal_worker.py` (re-exported here).

Every failure degrades to None / a log line: the caller then posts the
plain photo, as before this feature existed."""

import asyncio
import time

from loguru import logger
from telegram.ext import Application

from nani_pix_bot.db import session_scope
from nani_pix_bot.jobs import reveal_pregen, reveal_worker
from nani_pix_bot.jobs.avatars import fetch_avatar
from nani_pix_bot.jobs.reveal_pregen import start_pregeneration as start_pregeneration
from nani_pix_bot.jobs.reveal_worker import RevealCache
from nani_pix_bot.jobs.reveal_worker import start_worker as start_worker
from nani_pix_bot.jobs.reveal_worker import stop_worker as stop_worker
from nani_pix_bot.models.enums import GameStatus, RevealStatus
from nani_pix_bot.models.game import Game
from nani_pix_bot.models.reveal_video import RevealVideo
from nani_pix_bot.services.players import describe_person
from nani_pix_bot.services.reveal import pipeline, store

WAIT_SECONDS = 5.0
AVATAR_TIMEOUT = 3.0
RENDER_TIMEOUT = 10.0
_ACTIVE = (GameStatus.ACTIVE, GameStatus.VOTING)


def _fallback(game_id: int, reason: str) -> None:
    logger.info("reveal falls back to the photo: {reason}", reason=reason, game_id=game_id)


async def _await_future(
    future: asyncio.Future[pipeline.Pregen], game_id: int
) -> pipeline.Pregen | str:
    try:
        return await asyncio.wait_for(asyncio.shield(future), WAIT_SECONDS)
    except TimeoutError:
        logger.warning("gave up waiting for the reveal pre-render", game_id=game_id)
        return f"the pre-render wasn't done within {WAIT_SECONDS:g} s"
    except Exception:
        return "the pre-render failed"


def _row_pregen(row: RevealVideo) -> pipeline.Pregen | None:
    """The stored effect part of a READY slot row (render_ms isn't stored)."""
    if row.status != RevealStatus.READY or row.part1_ts is None or row.join_offset is None:
        return None
    return pipeline.Pregen(row.part1_ts, row.join_offset, 0)


def _stored_pregen(application: Application, game_id: int) -> pipeline.Pregen | None:
    """The READY slot of this game (after a restart the cache may be empty)."""
    with session_scope(application.bot_data["session_factory"]) as session:
        row = store.load(session)
        return _row_pregen(row) if row is not None and row.game_id == game_id else None


async def _find_pregen(
    application: Application, cache: RevealCache, game_id: int
) -> pipeline.Pregen | str:
    """The effect part for this game, or the reason there is none."""
    result: pipeline.Pregen | str | None = None
    if cache.game_id == game_id:
        if cache.pregen is not None:
            result = cache.pregen
        elif cache.future is not None:
            result = await _await_future(cache.future, game_id)
    if result is None:
        result = _stored_pregen(application, game_id)
    return result or "no pre-rendered video for this game"


async def _avatar(
    application: Application, game_id: int, winner_id: int, handle: str | None
) -> bytes | None:
    """The winner's avatar, or None (initials) when it takes over AVATAR_TIMEOUT."""
    try:
        return await asyncio.wait_for(fetch_avatar(application.bot, winner_id), AVATAR_TIMEOUT)
    except TimeoutError:
        label = handle or ""
        logger.warning(
            "the avatar of {winner} took over {seconds:g} s; using initials",
            seconds=AVATAR_TIMEOUT,
            winner=describe_person(
                winner_id,
                username=label[1:] if label.startswith("@") else None,
                name=None if label.startswith("@") else label or None,
            ),
            winner_id=winner_id,
            game_id=game_id,
        )
        return None


async def render_reveal(
    application: Application, game_id: int, clear: bytes, winner_id: int | None, handle: str | None
) -> bytes | None:
    """The final reveal MP4, or None when the caller should post the photo.

    `clear` must be the same stored bytes `start_pregeneration` pre-rendered
    from (the game's `original_image`, or the hard-mode image chosen for the
    slot): the worker's static cache only hits on byte-identical input.

    The bot handles one update at a time, so this is bounded: the avatar
    fetch overlaps the pre-render wait, and the render itself gets
    RENDER_TIMEOUT (worst case max(WAIT_SECONDS, AVATAR_TIMEOUT) + RENDER_TIMEOUT)."""
    cache = reveal_worker.runtime(application)
    if cache is None:
        _fallback(game_id, "no reveal worker")
        return None
    avatar_task = (
        asyncio.ensure_future(_avatar(application, game_id, winner_id, handle))
        if winner_id
        else None
    )
    try:
        pregen = await _find_pregen(application, cache, game_id)
        if isinstance(pregen, str):
            _fallback(game_id, pregen)
            return None
        badge = None if avatar_task is None else pipeline.Badge(await avatar_task, handle or "")
    finally:
        if avatar_task is not None:
            avatar_task.cancel()  # a no-op once finished; stops it on every other way out
    return await _finish(application, game_id, clear, pregen, badge)


async def _finish(
    application: Application,
    game_id: int,
    clear: bytes,
    pregen: pipeline.Pregen,
    badge: pipeline.Badge | None,
) -> bytes | None:
    started = time.perf_counter()
    try:
        video = await asyncio.wait_for(
            reveal_worker.submit(application.bot_data, pipeline.finish, pregen, clear, badge),
            RENDER_TIMEOUT,
        )
        # the worker process can't log with the game's context, so the timing is logged here
        logger.info(
            "reveal rendered in {ms} ms, {kib} KiB, celebration={celebration}",
            ms=round((time.perf_counter() - started) * 1000),
            kib=len(video) // 1024,
            celebration=badge is not None,
            game_id=game_id,
        )
        return video
    except TimeoutError:
        logger.warning(
            "the reveal render took over {seconds:g} s", seconds=RENDER_TIMEOUT, game_id=game_id
        )
        _fallback(game_id, f"the render wasn't done within {RENDER_TIMEOUT:g} s")
        return None
    except Exception as exc:
        logger.opt(exception=exc).error("reveal render failed", game_id=game_id)
        _fallback(game_id, "the render failed")
        return None


async def reload_on_startup(application: Application) -> None:
    """Restores the slot after a restart: a ready one goes back in the cache,
    a pending one of a live game is re-rendered, anything else is cleared."""
    cache = reveal_worker.runtime(application)
    if cache is None:
        return
    with session_scope(application.bot_data["session_factory"]) as session:
        row = store.load(session)
        if row is None:
            return
        game_id, status, ready_at = row.game_id, row.status, row.ready_at
        pregen = _row_pregen(row)
        game = session.get(Game, game_id)
        rerender = status == RevealStatus.PENDING and game is not None and game.status in _ACTIVE
        if pregen is None and not rerender:
            store.clear(session, game_id)
            logger.info(
                "cleared a stale reveal slot ({status})", status=status.value, game_id=game_id
            )
            return
    if pregen is not None:
        cache.ready(game_id, pregen)
        logger.info(
            "reloaded the pre-rendered reveal (ready at {ready_at})",
            ready_at=ready_at,
            game_id=game_id,
        )
    else:
        logger.info("re-rendering the pending reveal", game_id=game_id)
        reveal_pregen.start_pregeneration(application, game_id, startup=True)


def finished(application: Application, game_id: int) -> None:
    """The reveal has been sent (or skipped): free the slot and the cache."""
    with session_scope(application.bot_data["session_factory"]) as session:
        store.clear(session, game_id)
    cache = application.bot_data.get("reveal_cache")
    if isinstance(cache, RevealCache):
        cache.drop(game_id)
