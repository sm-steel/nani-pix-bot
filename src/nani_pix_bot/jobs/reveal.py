"""Telegram-side lifecycle of the animated reveal (#295): a one-worker
process pool for the CPU-heavy rendering, a one-entry in-memory cache of
the pre-rendered effect part, the fire-and-forget pre-render at game
start, the reveal-time render, and the startup reload from the DB slot.

Every failure degrades to None / a log line: the caller then posts the
plain photo, as before this feature existed."""

import asyncio
import multiprocessing
from concurrent.futures import Executor, ProcessPoolExecutor
from concurrent.futures.process import BrokenProcessPool
from dataclasses import dataclass
from typing import Any

from loguru import logger
from sqlalchemy.orm import Session
from telegram.ext import Application

from nani_pix_bot.db import session_scope
from nani_pix_bot.jobs.avatars import fetch_avatar
from nani_pix_bot.models.enums import GameStatus, PixelAlgorithm, RevealEffect, RevealStatus
from nani_pix_bot.models.game import Game
from nani_pix_bot.services.game import STAGE_ORDER
from nani_pix_bot.services.reveal import pipeline, store
from nani_pix_bot.services.settings.stage_config import get_stage_config

WAIT_SECONDS = 5.0
AVATAR_TIMEOUT = 3.0
_ACTIVE = (GameStatus.ACTIVE, GameStatus.VOTING)

_Inputs = tuple[bytes, PixelAlgorithm, tuple[int, ...], RevealEffect]


@dataclass
class RevealCache:
    """The pre-rendered effect part of the one game that can end next."""

    game_id: int | None = None
    pregen: pipeline.Pregen | None = None
    future: asyncio.Future[pipeline.Pregen] | None = None

    def pending(self, game_id: int, future: asyncio.Future[pipeline.Pregen]) -> None:
        """Replaces the entry; the previous bytes are dropped."""
        self.game_id, self.pregen, self.future = game_id, None, future

    def ready(self, game_id: int, pregen: pipeline.Pregen) -> bool:
        if self.game_id not in (None, game_id):
            return False
        self.game_id, self.pregen = game_id, pregen
        return True

    def drop(self, game_id: int) -> None:
        if self.game_id == game_id:
            self.game_id = self.pregen = self.future = None


def start_worker(bot_data: dict[str, Any]) -> None:
    """(Re)creates the render worker. A fresh spawn process imports the
    heavy modules in the background so the first real job doesn't pay."""
    old = bot_data.get("reveal_executor")
    if isinstance(old, Executor):
        old.shutdown(wait=False, cancel_futures=True)
    executor = ProcessPoolExecutor(max_workers=1, mp_context=multiprocessing.get_context("spawn"))
    bot_data["reveal_executor"] = executor
    executor.submit(pipeline.warm_up)


def stop_worker(bot_data: dict[str, Any]) -> None:
    executor = bot_data.pop("reveal_executor", None)
    if isinstance(executor, Executor):
        executor.shutdown(wait=False, cancel_futures=True)


async def _submit(bot_data: dict[str, Any], fn, *args):
    loop = asyncio.get_running_loop()
    try:
        return await loop.run_in_executor(bot_data["reveal_executor"], fn, *args)
    except BrokenProcessPool:
        logger.warning("the reveal worker died; starting a new one")
        start_worker(bot_data)
        raise


def _runtime(application: Application) -> RevealCache | None:
    """The cache, when this application has a real worker and cache (a bare
    mock `application` in other tests doesn't)."""
    executor = application.bot_data.get("reveal_executor")
    cache = application.bot_data.get("reveal_cache")
    if isinstance(executor, Executor) and isinstance(cache, RevealCache):
        return cache
    return None


def _read_inputs(session: Session, game_id: int) -> _Inputs | None:
    """The stored image bytes (unmodified: the worker's static cache keys on
    them), the pixelation algorithm, the five stage widths and the effect."""
    game = session.get(Game, game_id)
    slot = store.load(session)
    if game is None or slot is None or slot.game_id != game_id:
        return None
    if game.hard_mode:
        image = game.hard_mode_image_b if slot.image_choice == "b" else game.hard_mode_image_a
    else:
        image = game.original_image
    if image is None:
        return None
    widths = tuple(get_stage_config(session, stage).target_width for stage in STAGE_ORDER)
    return image, game.pixel_algorithm, widths, slot.effect


def start_pregeneration(application: Application, game_id: int) -> None:
    """Fire-and-forget: renders the effect part in the worker while the game is played."""
    cache = _runtime(application)
    if cache is None:
        logger.debug("no reveal worker; skipping the pre-render", game_id=game_id)
        return
    with session_scope(application.bot_data["session_factory"]) as session:
        inputs = _read_inputs(session, game_id)
    if inputs is None:
        logger.warning("nothing to pre-render for this game", game_id=game_id)
        return
    future: asyncio.Future[pipeline.Pregen] = asyncio.get_running_loop().create_future()
    cache.pending(game_id, future)
    application.create_task(_pregenerate(application, cache, game_id, future, inputs))


def _resolve(
    future: asyncio.Future[pipeline.Pregen], pregen: pipeline.Pregen | BaseException
) -> None:
    """Always settles the future so no waiter hangs; an exception is marked
    retrieved so asyncio doesn't log it when nobody was waiting."""
    if isinstance(pregen, BaseException):
        future.set_exception(pregen)
        future.exception()
    else:
        future.set_result(pregen)


async def _pregenerate(
    application: Application,
    cache: RevealCache,
    game_id: int,
    future: asyncio.Future[pipeline.Pregen],
    inputs: _Inputs,
) -> None:
    session_factory = application.bot_data["session_factory"]
    effect = inputs[3]
    try:
        pregen = await _submit(application.bot_data, pipeline.pregenerate, *inputs)
        with session_scope(session_factory) as session:
            stored = store.mark_ready(session, game_id, pregen.part1_ts, pregen.join_offset)
    except Exception as exc:
        logger.opt(exception=exc).error("reveal pre-render failed", game_id=game_id)
        with session_scope(session_factory) as session:
            if not store.mark_failed(session, game_id):
                logger.info("discarded a reveal render for an older game", game_id=game_id)
        _resolve(future, exc)
        return
    if stored and cache.ready(game_id, pregen):
        logger.info(
            "reveal pre-rendered ({effect}) in {ms} ms, {kib} KiB",
            effect=effect.value,
            ms=pregen.render_ms,
            kib=len(pregen.part1_ts) // 1024,
            game_id=game_id,
        )
    else:
        logger.info("discarded a reveal render for an older game", game_id=game_id)
    _resolve(future, pregen)


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


def _stored_pregen(application: Application, game_id: int) -> pipeline.Pregen | None:
    """The READY slot of this game (after a restart the cache may be empty)."""
    with session_scope(application.bot_data["session_factory"]) as session:
        row = store.load(session)
        if row is None or row.game_id != game_id or row.status != RevealStatus.READY:
            return None
        if row.part1_ts is None or row.join_offset is None:
            return None
        return pipeline.Pregen(row.part1_ts, row.join_offset, 0)


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


async def _badge(
    application: Application, winner_id: int | None, handle: str | None
) -> pipeline.Badge | None:
    if not winner_id:
        return None
    try:
        avatar = await asyncio.wait_for(fetch_avatar(application.bot, winner_id), AVATAR_TIMEOUT)
    except TimeoutError:
        logger.warning(
            "the winner's avatar took over {seconds:g} s; using initials", seconds=AVATAR_TIMEOUT
        )
        avatar = None
    return pipeline.Badge(avatar, handle or "")


async def render_reveal(
    application: Application, game_id: int, clear: bytes, winner_id: int | None, handle: str | None
) -> bytes | None:
    """The final reveal MP4, or None when the caller should post the photo.

    `clear` must be the same stored bytes `start_pregeneration` pre-rendered
    from (the game's `original_image`, or the hard-mode image chosen for the
    slot): the worker's static cache only hits on byte-identical input."""
    cache = _runtime(application)
    if cache is None:
        _fallback(game_id, "no reveal worker")
        return None
    pregen = await _find_pregen(application, cache, game_id)
    if isinstance(pregen, str):
        _fallback(game_id, pregen)
        return None
    badge = await _badge(application, winner_id, handle)
    try:
        return await _submit(application.bot_data, pipeline.finish, pregen, clear, badge)
    except Exception as exc:
        logger.opt(exception=exc).error("reveal render failed", game_id=game_id)
        _fallback(game_id, "the render failed")
        return None


async def reload_on_startup(application: Application) -> None:
    """Restores the slot after a restart: a ready one goes back in the cache,
    a pending one of a live game is re-rendered, anything else is cleared."""
    cache = _runtime(application)
    if cache is None:
        return
    with session_scope(application.bot_data["session_factory"]) as session:
        row = store.load(session)
        if row is None:
            return
        game_id, status, ready_at = row.game_id, row.status, row.ready_at
        pregen = None
        if (
            status == RevealStatus.READY
            and row.part1_ts is not None
            and row.join_offset is not None
        ):
            pregen = pipeline.Pregen(row.part1_ts, row.join_offset, 0)
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
        start_pregeneration(application, game_id)


def finished(application: Application, game_id: int) -> None:
    """The reveal has been sent (or skipped): free the slot and the cache."""
    with session_scope(application.bot_data["session_factory"]) as session:
        store.clear(session, game_id)
    cache = application.bot_data.get("reveal_cache")
    if isinstance(cache, RevealCache):
        cache.drop(game_id)
