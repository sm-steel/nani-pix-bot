import asyncio
from collections.abc import Iterator
from typing import TYPE_CHECKING

import pytest
from loguru import logger

from nani_pix_bot import log_context
from nani_pix_bot.models.enums import GameStatus
from nani_pix_bot.models.game import Game

if TYPE_CHECKING:
    from loguru import Logger

Captured = tuple[list[dict], "Logger"]


@pytest.fixture
def extras() -> Iterator[Captured]:
    """Each record's `extra` after the context patcher has run, and the
    patched logger that produces them."""
    captured: list[dict] = []
    log_context.reset()
    patched = logger.patch(log_context.patcher)
    sink_id = logger.add(lambda m: captured.append(dict(m.record["extra"])), level="DEBUG")
    yield captured, patched
    logger.remove(sink_id)
    log_context.reset()


def test_reset_replaces_the_whole_context(extras) -> None:
    captured, log = extras
    log_context.reset(user_id=1, game_id=9)
    log_context.reset(user_id=2)

    log.info("x")

    assert captured[-1] == {"user_id": 2}


def test_bind_merges_into_the_context(extras) -> None:
    captured, log = extras
    log_context.reset(user_id=1)
    log_context.bind(game_id=9)

    log.info("x")

    assert captured[-1] == {"user_id": 1, "game_id": 9}


def test_an_explicit_kwarg_wins_over_the_context(extras) -> None:
    captured, log = extras
    log_context.reset(game_id=9)

    log.info("refund for {game_id}", game_id=4)

    assert captured[-1]["game_id"] == 4


def test_bind_game_records_id_and_status(extras) -> None:
    captured, log = extras
    game = Game(id=88, starter_id=1, status=GameStatus.ACTIVE)

    log_context.bind_game(game)
    log.info("x")

    assert captured[-1] == {"game_id": 88, "game_status": "active"}


def test_bind_game_ignores_none(extras) -> None:
    captured, log = extras
    log_context.reset(user_id=1)

    log_context.bind_game(None)
    log.info("x")

    assert captured[-1] == {"user_id": 1}


async def test_context_is_isolated_between_tasks(extras) -> None:
    captured, log = extras

    async def run(game_id: int) -> None:
        log_context.reset(game_id=game_id)
        await asyncio.sleep(0)
        log.info("x")

    await asyncio.gather(run(1), run(2))

    assert sorted(e["game_id"] for e in captured) == [1, 2]
