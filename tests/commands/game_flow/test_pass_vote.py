from typing import cast
from unittest.mock import AsyncMock, MagicMock

import pytest
from sqlalchemy import select
from telegram import Update
from telegram.ext import ContextTypes

from nani_pix_bot.commands.game_flow import pass_vote as pass_module
from nani_pix_bot.models.enums import GameStatus, PixelStage
from nani_pix_bot.models.event_log import EventLog
from nani_pix_bot.models.game import Game
from nani_pix_bot.models.player import Player
from nani_pix_bot.services import i18n
from nani_pix_bot.services.game import guesses

HOST, BOT = 1, 999


def _game(session_factory, **overrides) -> int:
    with session_factory() as session:
        session.add_all(Player(telegram_user_id=i) for i in (HOST, BOT, 10, 11, 12))
        session.flush()
        fields = {
            "starter_id": HOST,
            "status": GameStatus.ACTIVE,
            "current_stage": PixelStage.STAGE_1,
            "title_english": "Frieren",
            "original_image": b"file123",
        } | overrides
        game = Game(**fields)
        session.add(game)
        session.commit()
        return game.id


def _update(user_id: int, *, thread_id: int | None = 7) -> MagicMock:
    update = MagicMock()
    update.effective_user.id = user_id
    update.effective_user.full_name = f"Player {user_id}"
    update.effective_chat.id = 555
    update.message.message_thread_id = thread_id
    update.effective_message = update.message
    update.message.reply_text = AsyncMock()
    return update


def _context(session_factory) -> MagicMock:
    context = MagicMock()
    context.bot.id = BOT
    context.bot_data = {
        "session_factory": session_factory,
        "group_chat_id": 555,
        "game_topic_id": 7,
    }
    context.bot.send_photo = AsyncMock(return_value=MagicMock(message_id=999))
    context.bot.send_media_group = AsyncMock(
        return_value=[MagicMock(message_id=998), MagicMock(message_id=999)]
    )
    context.bot.send_message = AsyncMock(return_value=MagicMock(message_id=77))
    context.bot.pin_chat_message = AsyncMock()
    context.bot.unpin_chat_message = AsyncMock()
    context.job_queue.get_jobs_by_name.return_value = []
    return context


async def _pass(session_factory, user_id: int, context=None, **update_kw) -> MagicMock:
    update = _update(user_id, **update_kw)
    await pass_module.pass_command(
        cast(Update, update), cast(ContextTypes.DEFAULT_TYPE, context or _context(session_factory))
    )
    return update


def _status(session_factory, game_id: int) -> GameStatus:
    with session_factory() as session:
        return session.get(Game, game_id).status


async def test_a_vote_is_counted_and_announced(session_factory, log_records) -> None:
    _game(session_factory)
    update = await _pass(session_factory, 10)
    text = update.message.reply_text.await_args.args[0]
    assert "1/3" in text
    assert any(r.level == "INFO" and r.extra.get("votes") == 1 for r in log_records)


async def test_the_same_command_again_withdraws(session_factory) -> None:
    _game(session_factory)
    await _pass(session_factory, 10)
    update = await _pass(session_factory, 10)
    reply = update.message.reply_text.await_args.args[0]
    assert reply == i18n.t("pass.withdrawn", "EN", name="Player 10", votes=0, needed=3)


async def test_the_host_is_refused(session_factory, log_records) -> None:
    _game(session_factory)
    update = await _pass(session_factory, HOST)
    update.message.reply_text.assert_awaited_once_with(i18n.t("pass.host", "EN"))
    assert any(r.level == "WARNING" for r in log_records)


@pytest.mark.parametrize("status", [GameStatus.SETUP, GameStatus.VOTING])
async def test_no_active_round_is_refused(session_factory, status) -> None:
    game_id = _game(session_factory, status=status)
    update = await _pass(session_factory, 10)
    update.message.reply_text.assert_awaited_once_with(i18n.t("pass.no_game", "EN"))
    assert _status(session_factory, game_id) is status


async def test_outside_the_game_topic_nothing_happens(session_factory) -> None:
    _game(session_factory)
    update = await _pass(session_factory, 10, thread_id=99)
    update.message.reply_text.assert_not_awaited()


async def test_the_third_vote_ends_the_game_unsolved_and_cancels_the_timeout(
    session_factory,
) -> None:
    game_id = _game(session_factory)
    context = _context(session_factory)
    for player in (10, 11, 12):
        await _pass(session_factory, player, context)
    assert _status(session_factory, game_id) is GameStatus.UNSOLVED
    context.bot.send_photo.assert_awaited_once()  # the reveal
    caption = context.bot.send_photo.await_args.kwargs["caption"]
    assert "🏳" in caption
    context.job_queue.get_jobs_by_name.assert_any_call(f"game-timeout-{game_id}")
    with session_factory() as session:
        causes = [e.data.get("cause") for e in session.scalars(select(EventLog)) if e.data]
    assert "pass vote (3/3)" in causes


async def test_hard_mode_with_guessers_opens_the_winner_vote(session_factory) -> None:
    game_id = _game(
        session_factory,
        current_stage=None,
        original_image=None,
        hard_mode=True,
        hard_mode_turn=1,
        hard_mode_image_a=b"a",
        hard_mode_image_b=b"b",
    )
    with session_factory() as session:
        game = session.get(Game, game_id)
        guesses.log_guess(
            session, game, guesses.GuessRecord(player_id=10, text="x", stage=1, correct=False)
        )
        session.commit()
    context = _context(session_factory)
    for player in (10, 11, 12):
        await _pass(session_factory, player, context)
    assert _status(session_factory, game_id) is GameStatus.VOTING
