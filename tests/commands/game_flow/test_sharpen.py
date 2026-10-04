from typing import cast
from unittest.mock import AsyncMock, MagicMock

import pytest
from telegram import Update
from telegram.error import BadRequest
from telegram.ext import ContextTypes

from nani_pix_bot.commands.game_flow import sharpen as sharpen_module
from nani_pix_bot.commands.game_flow import stage_post
from nani_pix_bot.jobs import timers as timeout_module
from nani_pix_bot.models import CurrencyTransfer
from nani_pix_bot.models.enums import GameStatus, PixelStage
from nani_pix_bot.models.game import Game
from nani_pix_bot.models.player import Player
from nani_pix_bot.services import i18n

STARTER, ALICE, BOB = 1, 2, 3


def _update(*, user_id: int = ALICE, thread_id: int | None = 7) -> MagicMock:
    update = MagicMock()
    update.effective_user.id = user_id
    update.effective_user.username = "alice"
    update.effective_user.full_name = "Alice"
    update.effective_chat.id = 555
    update.message.message_thread_id = thread_id
    update.effective_message = update.message
    update.message.reply_text = AsyncMock()
    return update


def _callback_update(data: str, *, user_id: int = ALICE) -> MagicMock:
    update = MagicMock()
    update.callback_query.data = data
    update.callback_query.from_user.id = user_id
    update.callback_query.from_user.username = "alice"
    update.callback_query.from_user.full_name = "Alice"
    update.callback_query.answer = AsyncMock()
    update.callback_query.edit_message_text = AsyncMock()
    return update


def _context(session_factory) -> MagicMock:
    context = MagicMock()
    context.bot_data = {
        "session_factory": session_factory,
        "group_chat_id": 555,
        "game_topic_id": 7,
        "bot_username": "nani_bot",
    }
    context.job_queue.get_jobs_by_name.return_value = []
    context.bot.send_photo = AsyncMock(return_value=MagicMock(message_id=999))
    context.bot.pin_chat_message = AsyncMock()
    context.bot.unpin_chat_message = AsyncMock()
    return context


def _seed(session_factory, *, currency: int = 100, **game_overrides) -> int:
    with session_factory() as session:
        session.add_all(
            [
                Player(telegram_user_id=STARTER),
                Player(telegram_user_id=ALICE, currency=currency),
                Player(telegram_user_id=BOB, currency=100),
            ]
        )
        session.commit()
        defaults = {
            "starter_id": STARTER,
            "original_image": b"file123",
            "status": GameStatus.ACTIVE,
            "current_stage": PixelStage.STAGE_1,
        }
        defaults.update(game_overrides)
        game = Game(**defaults)
        session.add(game)
        session.commit()
        return game.id


def _confirm_data(game_id: int, stage: str = "stage_1", user_id: int = ALICE) -> str:
    return f"sharpen:ok:{game_id}:{stage}:{user_id}"


async def _command(session_factory, **kwargs) -> MagicMock:
    update = _update(**kwargs)
    await sharpen_module.sharpen_command(
        cast(Update, update), cast(ContextTypes.DEFAULT_TYPE, _context(session_factory))
    )
    return update


async def _callback(session_factory, data: str, *, user_id: int = ALICE):
    update = _callback_update(data, user_id=user_id)
    context = _context(session_factory)
    await sharpen_module.sharpen_callback_handler(
        cast(Update, update), cast(ContextTypes.DEFAULT_TYPE, context)
    )
    return update, context


def _state(session_factory) -> tuple[PixelStage | None, int, int]:
    with session_factory() as session:
        game = session.query(Game).one()
        alice = session.get(Player, ALICE)
        assert alice is not None
        return game.current_stage, alice.currency, session.query(CurrencyTransfer).count()


@pytest.fixture(autouse=True)
def _fake_pixelate(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(stage_post.pixelate_service, "pixelate", lambda *_: b"pixelated")


async def test_command_replies_with_a_confirm_button(session_factory) -> None:
    game_id = _seed(session_factory)

    update = await _command(session_factory)

    update.message.reply_text.assert_awaited_once()
    call = update.message.reply_text.await_args
    assert call.args[0] == i18n.t("sharpen.confirm_prompt", "en", price=50)
    buttons = [b for row in call.kwargs["reply_markup"].inline_keyboard for b in row]
    assert buttons[0].callback_data == _confirm_data(game_id)
    assert buttons[0].text == i18n.t("sharpen.confirm_button", "en", price=50)
    assert buttons[1].callback_data == f"sharpen:no:{ALICE}"
    assert all(len(b.callback_data.encode()) <= 64 for b in buttons)


async def test_command_outside_the_game_topic_is_ignored(session_factory) -> None:
    _seed(session_factory)

    update = await _command(session_factory, thread_id=99)

    update.message.reply_text.assert_not_awaited()


@pytest.mark.parametrize(
    ("overrides", "user_id", "key"),
    [
        ({"hard_mode": True, "current_stage": None}, ALICE, "hard_mode"),
        ({"current_stage": PixelStage.STAGE_5}, ALICE, "last_stage"),
        ({}, STARTER, "setter"),
        ({"status": GameStatus.SETUP}, ALICE, "no_game"),
    ],
)
async def test_command_refusals_have_no_button(
    session_factory, overrides: dict, user_id: int, key: str
) -> None:
    _seed(session_factory, **overrides)

    update = await _command(session_factory, user_id=user_id)

    call = update.message.reply_text.await_args
    assert call.args[0] == i18n.t(f"sharpen.refusal.{key}", "en")
    assert "reply_markup" not in call.kwargs


async def test_command_with_no_game_is_refused(session_factory) -> None:
    update = await _command(session_factory)

    assert update.message.reply_text.await_args.args[0] == i18n.t("sharpen.refusal.no_game", "en")


async def test_confirm_charges_advances_and_posts_the_stage_photo(session_factory) -> None:
    game_id = _seed(session_factory)

    update, context = await _callback(session_factory, _confirm_data(game_id))

    assert _state(session_factory) == (PixelStage.STAGE_2, 50, 1)
    context.bot.send_photo.assert_awaited_once()
    kwargs = context.bot.send_photo.await_args.kwargs
    assert kwargs["photo"] == b"pixelated"
    assert "🔍" in kwargs["caption"]
    assert "Alice" in kwargs["caption"]
    assert kwargs["reply_markup"].inline_keyboard[0][0].url.startswith("https://t.me/nani_bot")
    update.callback_query.edit_message_text.assert_awaited_once_with(
        i18n.t("sharpen.done_short", "en")
    )
    update.callback_query.answer.assert_awaited()
    names = [call.kwargs["name"] for call in context.job_queue.run_once.call_args_list]
    assert timeout_module.inactivity_nudge_job_name(game_id) in names
    assert timeout_module.inactivity_advance_job_name(game_id) in names


async def test_confirm_survives_an_unmodifiable_message(session_factory) -> None:
    game_id = _seed(session_factory)
    update = _callback_update(_confirm_data(game_id))
    update.callback_query.edit_message_text = AsyncMock(
        side_effect=BadRequest("Message is not modified")
    )
    context = _context(session_factory)

    await sharpen_module.sharpen_callback_handler(
        cast(Update, update), cast(ContextTypes.DEFAULT_TYPE, context)
    )

    context.bot.send_photo.assert_awaited_once()
    assert _state(session_factory)[0] is PixelStage.STAGE_2


async def test_only_requester_can_confirm(session_factory) -> None:
    game_id = _seed(session_factory)

    update, context = await _callback(session_factory, _confirm_data(game_id), user_id=BOB)

    update.callback_query.answer.assert_awaited_once_with(
        i18n.t("sharpen.not_yours", "en"), show_alert=True
    )
    context.bot.send_photo.assert_not_awaited()
    assert _state(session_factory) == (PixelStage.STAGE_1, 100, 0)


async def test_stale_confirm_alerts_and_charges_nothing(session_factory) -> None:
    game_id = _seed(session_factory, current_stage=PixelStage.STAGE_2)

    update, context = await _callback(session_factory, _confirm_data(game_id, "stage_1"))

    update.callback_query.answer.assert_awaited_once_with(
        i18n.t("sharpen.refusal.stale", "en"), show_alert=True
    )
    context.bot.send_photo.assert_not_awaited()
    assert _state(session_factory) == (PixelStage.STAGE_2, 100, 0)


async def test_insufficient_funds_alert_shows_the_balance(session_factory) -> None:
    game_id = _seed(session_factory, currency=20)

    update, context = await _callback(session_factory, _confirm_data(game_id))

    update.callback_query.answer.assert_awaited_once_with(
        i18n.t("sharpen.refusal.insufficient", "en", balance=20), show_alert=True
    )
    context.bot.send_photo.assert_not_awaited()
    assert _state(session_factory) == (PixelStage.STAGE_1, 20, 0)


async def test_confirm_for_another_game_is_refused(session_factory) -> None:
    game_id = _seed(session_factory)

    update, _ = await _callback(session_factory, _confirm_data(game_id + 1))

    update.callback_query.answer.assert_awaited_once_with(
        i18n.t("sharpen.refusal.no_game", "en"), show_alert=True
    )
    assert _state(session_factory) == (PixelStage.STAGE_1, 100, 0)


async def test_cancel_edits_the_message(session_factory) -> None:
    _seed(session_factory)

    update, _ = await _callback(session_factory, f"sharpen:no:{ALICE}")

    update.callback_query.edit_message_text.assert_awaited_once_with(
        i18n.t("sharpen.cancelled", "en")
    )
    assert _state(session_factory) == (PixelStage.STAGE_1, 100, 0)


async def test_only_requester_can_cancel(session_factory) -> None:
    _seed(session_factory)

    update, _ = await _callback(session_factory, f"sharpen:no:{ALICE}", user_id=BOB)

    update.callback_query.edit_message_text.assert_not_awaited()
    update.callback_query.answer.assert_awaited_once_with(
        i18n.t("sharpen.not_yours", "en"), show_alert=True
    )


@pytest.mark.parametrize(
    "data",
    [
        "sharpen:",
        "sharpen:ok",
        "sharpen:ok:1:stage_1",
        "sharpen:ok:x:stage_1:2",
        "sharpen:ok:1:stage_9:2",
        "sharpen:ok:1:stage_1:2:3",
        "sharpen:no",
        "sharpen:no:abc",
        "sharpen:maybe:2",
    ],
)
async def test_malformed_data_is_answered_silently(session_factory, data: str) -> None:
    _seed(session_factory)

    update, context = await _callback(session_factory, data)

    update.callback_query.answer.assert_awaited_once_with()
    update.callback_query.edit_message_text.assert_not_awaited()
    context.bot.send_photo.assert_not_awaited()
    assert _state(session_factory) == (PixelStage.STAGE_1, 100, 0)
