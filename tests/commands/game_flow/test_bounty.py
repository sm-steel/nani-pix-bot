from typing import cast
from unittest.mock import AsyncMock, MagicMock

import pytest
from loguru import logger
from telegram import Update
from telegram.ext import ContextTypes

from nani_pix_bot.commands.game_flow import bounty as bounty_module
from nani_pix_bot.models.enums import GameStatus, PixelStage
from nani_pix_bot.models.game import Game
from nani_pix_bot.models.player import Player
from nani_pix_bot.services.economy import bounty as bounty_service

STARTER, ALICE = 1, 2


def _update(*, user_id: int = ALICE, thread_id: int | None = 7) -> MagicMock:
    update = MagicMock()
    update.effective_user.id = user_id
    update.effective_user.username = None
    update.effective_user.full_name = "Alice"
    update.effective_chat.id = 555
    update.message.message_thread_id = thread_id
    update.effective_message = update.message
    update.message.reply_text = AsyncMock()
    return update


def _context(session_factory, args: list[str]) -> MagicMock:
    context = MagicMock()
    context.bot_data = {
        "session_factory": session_factory,
        "group_chat_id": 555,
        "game_topic_id": 7,
    }
    context.args = args
    return context


def _seed(session_factory, *, game: bool = True, currency: int = 100) -> None:
    with session_factory() as session:
        session.add_all(
            [Player(telegram_user_id=STARTER), Player(telegram_user_id=ALICE, currency=currency)]
        )
        session.commit()
        if game:
            session.add(
                Game(
                    starter_id=STARTER,
                    original_image=b"f",
                    status=GameStatus.ACTIVE,
                    current_stage=PixelStage.STAGE_1,
                )
            )
            session.commit()


async def _run(session_factory, args: list[str], **update_kwargs) -> MagicMock:
    update = _update(**update_kwargs)
    await bounty_module.bounty_command(
        cast(Update, update), cast(ContextTypes.DEFAULT_TYPE, _context(session_factory, args))
    )
    return update


def _reply(update: MagicMock) -> str:
    update.message.reply_text.assert_awaited_once()
    return update.message.reply_text.await_args.args[0]


def _state(session_factory, user_id: int = ALICE) -> tuple[int, int]:
    with session_factory() as session:
        game = session.query(Game).one()
        player = session.get(Player, user_id)
        assert player is not None
        return player.currency, bounty_service.pot_balance(session, game.id)


async def test_valid_bounty_charges_and_fills_the_pot(session_factory) -> None:
    _seed(session_factory)
    update = await _run(session_factory, ["30"])

    assert "30 💠" in _reply(update)
    assert _state(session_factory) == (70, 30)


async def test_below_minimum_is_refused_without_charge(session_factory) -> None:
    _seed(session_factory)
    update = await _run(session_factory, ["5"])

    assert str(bounty_service.BOUNTY_MIN) in _reply(update)
    assert _state(session_factory) == (100, 0)


async def test_no_game_gets_the_no_game_reply(session_factory) -> None:
    _seed(session_factory, game=False)
    update = await _run(session_factory, ["30"])

    assert "No round" in _reply(update)


@pytest.mark.parametrize("args", [[], ["abc"], ["-5"], ["0"], ["1", "2"], ["5.5"]])
async def test_bad_args_get_the_usage_reply(session_factory, args: list[str]) -> None:
    _seed(session_factory)
    update = await _run(session_factory, args)

    assert "Usage: /bounty" in _reply(update)
    assert f"at least {bounty_service.BOUNTY_MIN} " in _reply(update)
    assert _state(session_factory) == (100, 0)


async def test_bad_args_are_logged_as_a_warning(session_factory) -> None:
    _seed(session_factory)
    captured: list[str] = []
    sink_id = logger.add(captured.append, level="WARNING", format="{message}")
    try:
        await _run(session_factory, ["abc"])
    finally:
        logger.remove(sink_id)

    assert any("malformed /bounty" in line for line in captured)


async def test_outside_the_topic_is_ignored(session_factory) -> None:
    _seed(session_factory)
    update = await _run(session_factory, ["30"], thread_id=999)

    update.message.reply_text.assert_not_awaited()
    assert _state(session_factory) == (100, 0)


async def test_insufficient_funds_reply_names_the_balance(session_factory) -> None:
    _seed(session_factory, currency=7)
    update = await _run(session_factory, ["30"])

    assert "7 💠" in _reply(update)
    assert _state(session_factory) == (7, 0)


async def test_the_setter_can_contribute(session_factory) -> None:
    _seed(session_factory)
    with session_factory() as session:
        player = session.get(Player, STARTER)
        assert player is not None
        player.currency = 100
        session.commit()

    update = await _run(session_factory, ["40"], user_id=STARTER)

    assert "40 💠" in _reply(update)
    assert _state(session_factory, STARTER) == (60, 40)
