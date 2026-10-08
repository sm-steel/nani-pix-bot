"""The reveal's render worker and one-entry cache (#295): a one-worker
spawn process pool for the CPU-heavy rendering, and the in-memory copy of
the pre-rendered effect part of the one game that can end next."""

import asyncio
import multiprocessing
from concurrent.futures import Executor, Future, ProcessPoolExecutor
from concurrent.futures.process import BrokenProcessPool
from dataclasses import dataclass
from typing import Any

from loguru import logger
from telegram.ext import Application

from nani_pix_bot.services.reveal import pipeline


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


def runtime(application: Application) -> RevealCache | None:
    """The cache, when this application has a real worker and cache (a bare
    mock `application` in other tests doesn't)."""
    executor = application.bot_data.get("reveal_executor")
    cache = application.bot_data.get("reveal_cache")
    if isinstance(executor, Executor) and isinstance(cache, RevealCache):
        return cache
    return None


def _log_warm_up(future: Future[None]) -> None:
    if not future.cancelled() and (exc := future.exception()) is not None:
        logger.opt(exception=exc).error("the reveal worker's warm-up failed")


def start_worker(bot_data: dict[str, Any]) -> None:
    """(Re)creates the render worker. A fresh spawn process imports the
    heavy modules in the background so the first real job doesn't pay."""
    old = bot_data.get("reveal_executor")
    if isinstance(old, Executor):
        old.shutdown(wait=False, cancel_futures=True)
    executor = ProcessPoolExecutor(max_workers=1, mp_context=multiprocessing.get_context("spawn"))
    bot_data["reveal_executor"] = executor
    executor.submit(pipeline.warm_up).add_done_callback(_log_warm_up)


def stop_worker(bot_data: dict[str, Any]) -> None:
    """Shuts the worker down without waiting for an in-flight render."""
    executor = bot_data.pop("reveal_executor", None)
    if not isinstance(executor, Executor):
        return
    # `_processes` is private to ProcessPoolExecutor; a missing attribute is a no-op
    processes = list((getattr(executor, "_processes", None) or {}).values())
    executor.shutdown(wait=False, cancel_futures=True)
    for process in processes:
        process.terminate()


async def submit(bot_data: dict[str, Any], fn, *args):
    executor = bot_data["reveal_executor"]
    try:
        return await asyncio.get_running_loop().run_in_executor(executor, fn, *args)
    except BrokenProcessPool:
        # concurrent submits all see the same dead pool; only the first replaces it
        if bot_data.get("reveal_executor") is executor:
            logger.warning("the reveal worker died; starting a new one")
            start_worker(bot_data)
        raise
