"""Game-start pre-render of the animated reveal (#295): reads what the
render needs, hands it to the worker without blocking the caller and records
the outcome in the DB slot and the one-entry cache. Every failure degrades to
a log line plus a FAILED slot: the reveal then posts the plain photo."""

import asyncio

from loguru import logger
from sqlalchemy.orm import Session
from telegram.ext import Application

from nani_pix_bot.db import session_scope
from nani_pix_bot.jobs import reveal_worker
from nani_pix_bot.jobs.reveal_worker import RevealCache, runtime
from nani_pix_bot.models.enums import PixelAlgorithm, RevealEffect
from nani_pix_bot.models.game import Game
from nani_pix_bot.services.game import STAGE_ORDER
from nani_pix_bot.services.reveal import pipeline, store
from nani_pix_bot.services.settings.stage_config import get_stage_config

_Inputs = tuple[bytes, PixelAlgorithm, tuple[int, ...], RevealEffect]


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


def _mark_failed(session_factory, game_id: int) -> None:
    """Marks the slot FAILED; never raises (it runs on error paths)."""
    try:
        with session_scope(session_factory) as session:
            if not store.mark_failed(session, game_id):
                logger.info("discarded a reveal render for an older game", game_id=game_id)
    except Exception:
        logger.opt(exception=True).error("couldn't mark the reveal slot failed", game_id=game_id)


# Strong references to the startup re-render tasks (the loop only keeps weak ones).
_startup_tasks: set[asyncio.Task[None]] = set()


def _spawn(application: Application, coro, startup: bool) -> None:
    """Runs `coro` in the background. `Application.create_task` before the application is
    running makes PTB warn that the task won't be awaited automatically, so the startup
    re-render (called from `post_init`) goes straight onto the loop."""
    if not startup:
        application.create_task(coro)
        return
    task = asyncio.get_running_loop().create_task(coro)
    _startup_tasks.add(task)
    task.add_done_callback(_startup_tasks.discard)


def start_pregeneration(application: Application, game_id: int, *, startup: bool = False) -> None:
    """Fire-and-forget: renders the effect part in the worker while the game is played.
    `startup` is for the re-render from `post_init`, before the application is running.
    Never raises into the caller."""
    cache = runtime(application)
    if cache is None:
        logger.debug("no reveal worker; skipping the pre-render", game_id=game_id)
        return
    session_factory = application.bot_data["session_factory"]
    try:
        with session_scope(session_factory) as session:
            inputs = _read_inputs(session, game_id)
    except Exception:
        logger.opt(exception=True).error("couldn't read the reveal inputs", game_id=game_id)
        _mark_failed(session_factory, game_id)
        return
    if inputs is None:
        logger.warning("nothing to pre-render for this game", game_id=game_id)
        _mark_failed(session_factory, game_id)
        return
    future: asyncio.Future[pipeline.Pregen] = asyncio.get_running_loop().create_future()
    cache.pending(game_id, future)
    coro = _pregenerate(application, cache, game_id, future, inputs)
    try:
        _spawn(application, coro, startup)
    except Exception as exc:
        coro.close()
        logger.opt(exception=exc).error("couldn't schedule the pre-render", game_id=game_id)
        _resolve(future, exc)


def _resolve(
    future: asyncio.Future[pipeline.Pregen], pregen: pipeline.Pregen | BaseException
) -> None:
    """Always settles the future so no waiter hangs; an exception is marked
    retrieved so asyncio doesn't log it when nobody was waiting."""
    if future.done():
        return
    if isinstance(pregen, BaseException):
        future.set_exception(pregen)
        future.exception()
    else:
        future.set_result(pregen)


def _log_stored(cache: RevealCache, game_id: int, effect: RevealEffect, pregen, stored) -> None:
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


async def _pregenerate(
    application: Application,
    cache: RevealCache,
    game_id: int,
    future: asyncio.Future[pipeline.Pregen],
    inputs: _Inputs,
) -> None:
    session_factory = application.bot_data["session_factory"]
    # what waiters see if this task is cancelled before it finishes
    outcome: pipeline.Pregen | BaseException = RuntimeError("the pre-render was cancelled")
    try:
        pregen = await reveal_worker.submit(application.bot_data, pipeline.pregenerate, *inputs)
        with session_scope(session_factory) as session:
            stored = store.mark_ready(session, game_id, pregen.part1_ts, pregen.join_offset)
        outcome = pregen
        _log_stored(cache, game_id, inputs[3], pregen, stored)
    except Exception as exc:
        outcome = exc
        logger.opt(exception=exc).error("reveal pre-render failed", game_id=game_id)
        _mark_failed(session_factory, game_id)
    finally:
        _resolve(future, outcome)
