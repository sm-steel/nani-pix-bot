from typing import cast
from unittest.mock import AsyncMock, MagicMock

from telegram import Update
from telegram.constants import ChatMemberStatus
from telegram.ext import ContextTypes

from nani_pix_bot.commands import stageconfig as stageconfig_module
from nani_pix_bot.models.bot_settings import BotSettings
from nani_pix_bot.models.enums import GameStatus, PixelStage
from nani_pix_bot.models.game import Game
from nani_pix_bot.models.player import Player
from nani_pix_bot.models.stage_config import StageConfig


def _make_update(*, user_id: int = 1, chat_type: str = "private") -> MagicMock:
    update = MagicMock()
    update.effective_user.id = user_id
    update.effective_chat.type = chat_type
    update.message.chat_id = 1
    update.message.message_thread_id = None
    update.message.reply_text = AsyncMock()
    return update


def _make_context(session_factory, *, admin_ids: set[int] | None = None, args=None) -> MagicMock:
    admin_ids = admin_ids or set()
    context = MagicMock()
    context.bot_data = {"session_factory": session_factory, "group_chat_id": 555}
    context.args = args or []
    context.bot.send_media_group = AsyncMock()

    async def _get_chat_member(_chat_id, user_id):
        status = ChatMemberStatus.ADMINISTRATOR if user_id in admin_ids else ChatMemberStatus.MEMBER
        return MagicMock(status=status)

    context.bot.get_chat_member = AsyncMock(side_effect=_get_chat_member)
    return context


def _seed_config(session_factory) -> None:
    with session_factory() as session:
        session.add(StageConfig(stage=PixelStage.STAGE_1, target_width=64, wrong_guess_limit=1))
        session.commit()


def _set_russian(session_factory) -> None:
    with session_factory() as session:
        session.add(BotSettings(id=1, language="RU"))
        session.commit()


def _active_game(session_factory, *, starter_id: int = 1) -> int:
    with session_factory() as session:
        session.add(Player(telegram_user_id=starter_id))
        session.commit()
        game = Game(
            starter_id=starter_id,
            original_file_id="file123",
            status=GameStatus.ACTIVE,
            current_stage=PixelStage.STAGE_1,
        )
        session.add(game)
        session.commit()
        return game.id


async def test_stageconfig_command_rejects_non_admin(session_factory) -> None:
    update = _make_update(user_id=2)
    context = _make_context(session_factory)

    await stageconfig_module.stageconfig_command(
        cast(Update, update), cast(ContextTypes.DEFAULT_TYPE, context)
    )

    update.message.reply_text.assert_awaited_once()
    args, kwargs = update.message.reply_text.await_args
    assert "admin" in args[0].lower()
    assert "parse_mode" not in kwargs


async def test_stageconfig_command_rejects_non_admin_in_russian(session_factory) -> None:
    _set_russian(session_factory)
    update = _make_update(user_id=2)
    context = _make_context(session_factory)

    await stageconfig_module.stageconfig_command(
        cast(Update, update), cast(ContextTypes.DEFAULT_TYPE, context)
    )

    update.message.reply_text.assert_awaited_once()
    reply_text = update.message.reply_text.await_args.args[0]
    assert reply_text == "Только для админов."


async def test_stageconfig_command_shows_russian_table_headers(session_factory) -> None:
    _seed_config(session_factory)
    _set_russian(session_factory)
    update = _make_update(user_id=1)
    context = _make_context(session_factory, admin_ids={1})

    await stageconfig_module.stageconfig_command(
        cast(Update, update), cast(ContextTypes.DEFAULT_TYPE, context)
    )

    args, _ = update.message.reply_text.await_args
    assert "Этап" in args[0]
    assert "Ширина" in args[0]
    assert "Попытки" in args[0]


async def test_stageconfig_command_ignores_group_chat(session_factory) -> None:
    update = _make_update(user_id=1, chat_type="supergroup")
    context = _make_context(session_factory, admin_ids={1})

    await stageconfig_module.stageconfig_command(
        cast(Update, update), cast(ContextTypes.DEFAULT_TYPE, context)
    )

    update.message.reply_text.assert_not_awaited()


async def test_stageconfig_command_shows_table_for_admin(session_factory) -> None:
    _seed_config(session_factory)
    update = _make_update(user_id=1)
    context = _make_context(session_factory, admin_ids={1})

    await stageconfig_module.stageconfig_command(
        cast(Update, update), cast(ContextTypes.DEFAULT_TYPE, context)
    )

    update.message.reply_text.assert_awaited_once()
    args, kwargs = update.message.reply_text.await_args
    assert "STAGE_1" in args[0]
    assert "64" in args[0]
    assert kwargs["parse_mode"] == "HTML"


async def test_setstageconfig_command_rejects_non_admin(session_factory) -> None:
    update = _make_update(user_id=2)
    context = _make_context(session_factory, args=["64:1", "80:1", "128:2", "192:3", "512:3"])

    await stageconfig_module.setstageconfig_command(
        cast(Update, update), cast(ContextTypes.DEFAULT_TYPE, context)
    )

    update.message.reply_text.assert_awaited_once()
    assert "admin" in update.message.reply_text.await_args.args[0].lower()


async def test_setstageconfig_command_rejects_wrong_pair_count(session_factory) -> None:
    update = _make_update(user_id=1)
    context = _make_context(session_factory, admin_ids={1}, args=["64:1", "80:1"])

    await stageconfig_module.setstageconfig_command(
        cast(Update, update), cast(ContextTypes.DEFAULT_TYPE, context)
    )

    update.message.reply_text.assert_awaited_once()
    assert "usage" in update.message.reply_text.await_args.args[0].lower()


async def test_setstageconfig_command_rejects_invalid_pair(session_factory) -> None:
    update = _make_update(user_id=1)
    context = _make_context(
        session_factory, admin_ids={1}, args=["64:1", "80:1", "nope", "192:3", "512:3"]
    )

    await stageconfig_module.setstageconfig_command(
        cast(Update, update), cast(ContextTypes.DEFAULT_TYPE, context)
    )

    update.message.reply_text.assert_awaited_once()
    assert "usage" in update.message.reply_text.await_args.args[0].lower()


async def test_setstageconfig_command_blocks_while_a_game_is_running(session_factory) -> None:
    _active_game(session_factory)
    update = _make_update(user_id=1)
    context = _make_context(
        session_factory, admin_ids={1}, args=["64:1", "80:1", "128:2", "192:3", "512:3"]
    )

    await stageconfig_module.setstageconfig_command(
        cast(Update, update), cast(ContextTypes.DEFAULT_TYPE, context)
    )

    update.message.reply_text.assert_awaited_once()
    _, kwargs = update.message.reply_text.await_args
    assert "reply_markup" in kwargs
    context.bot.send_media_group.assert_not_awaited()
    with session_factory() as session:
        # unchanged — the edit was rejected before applying anything
        assert session.get(StageConfig, PixelStage.STAGE_1) is None


async def test_setstageconfig_command_applies_and_shows_preview(session_factory) -> None:
    update = _make_update(user_id=1)
    context = _make_context(
        session_factory, admin_ids={1}, args=["64:1", "80:1", "128:2", "192:3", "512:3"]
    )

    await stageconfig_module.setstageconfig_command(
        cast(Update, update), cast(ContextTypes.DEFAULT_TYPE, context)
    )

    with session_factory() as session:
        fetched = session.get(StageConfig, PixelStage.STAGE_1)
        assert fetched is not None
        assert fetched.target_width == 64
        assert fetched.wrong_guess_limit == 1
        fetched_5 = session.get(StageConfig, PixelStage.STAGE_5)
        assert fetched_5 is not None
        assert fetched_5.target_width == 512

    update.message.reply_text.assert_awaited_once()
    reply_args, reply_kwargs = update.message.reply_text.await_args
    assert reply_kwargs["parse_mode"] == "HTML"
    assert "STAGE_1" in reply_args[0]

    # One media-group album per stage (5 stages changed).
    assert context.bot.send_media_group.await_count == 5


async def test_setstage_command_rejects_out_of_range_stage_number(session_factory) -> None:
    update = _make_update(user_id=1)
    context = _make_context(session_factory, admin_ids={1}, args=["9", "100", "2"])

    await stageconfig_module.setstage_command(
        cast(Update, update), cast(ContextTypes.DEFAULT_TYPE, context)
    )

    update.message.reply_text.assert_awaited_once()
    assert "usage" in update.message.reply_text.await_args.args[0].lower()


async def test_setstage_command_applies_a_single_stage_and_shows_one_preview(
    session_factory,
) -> None:
    update = _make_update(user_id=1)
    context = _make_context(session_factory, admin_ids={1}, args=["3", "100", "2"])

    await stageconfig_module.setstage_command(
        cast(Update, update), cast(ContextTypes.DEFAULT_TYPE, context)
    )

    with session_factory() as session:
        fetched = session.get(StageConfig, PixelStage.STAGE_3)
        assert fetched is not None
        assert fetched.target_width == 100
        assert fetched.wrong_guess_limit == 2

    context.bot.send_media_group.assert_awaited_once()
    _, media_kwargs = context.bot.send_media_group.await_args
    assert "100px" in media_kwargs["media"][0].caption
    assert "2" in media_kwargs["media"][0].caption


async def test_setstage_command_preview_caption_is_translated(session_factory) -> None:
    _set_russian(session_factory)
    update = _make_update(user_id=1)
    context = _make_context(session_factory, admin_ids={1}, args=["3", "100", "2"])

    await stageconfig_module.setstage_command(
        cast(Update, update), cast(ContextTypes.DEFAULT_TYPE, context)
    )

    _, media_kwargs = context.bot.send_media_group.await_args
    assert "неверных попыток" in media_kwargs["media"][0].caption


async def test_setstage_command_blocks_while_a_game_is_running(session_factory) -> None:
    _active_game(session_factory)
    update = _make_update(user_id=1)
    context = _make_context(session_factory, admin_ids={1}, args=["3", "100", "2"])

    await stageconfig_module.setstage_command(
        cast(Update, update), cast(ContextTypes.DEFAULT_TYPE, context)
    )

    update.message.reply_text.assert_awaited_once()
    _, kwargs = update.message.reply_text.await_args
    assert "reply_markup" in kwargs
    context.bot.send_media_group.assert_not_awaited()


async def test_setstage_command_blocks_with_translated_message_in_russian(session_factory) -> None:
    _active_game(session_factory)
    _set_russian(session_factory)
    update = _make_update(user_id=1)
    context = _make_context(session_factory, admin_ids={1}, args=["3", "100", "2"])

    await stageconfig_module.setstage_command(
        cast(Update, update), cast(ContextTypes.DEFAULT_TYPE, context)
    )

    reply_text = update.message.reply_text.await_args.args[0]
    assert (
        reply_text == "Сейчас идёт игра — сначала остановите её, чтобы изменить настройки этапов."
    )
