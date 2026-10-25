"""👥 Someone else under your own /achievements list: the button, the player
picker it opens, and a pick showing that player's list the way
/achievements @user does (the browser in DM, the summary in the topic)."""

from typing import cast
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from telegram import Message, Update
from telegram.constants import ChatMemberStatus, ChatType
from telegram.ext import ContextTypes

from nani_pix_bot.commands import achievements
from nani_pix_bot.commands.achievements import browser, someone_else_picker
from nani_pix_bot.db import session_scope
from nani_pix_bot.models.player import Player
from nani_pix_bot.seasons.mechanics import HexKind
from nani_pix_bot.services import i18n
from nani_pix_bot.services.achievements import engine
from tests.hex_fixtures import add_hex

pytestmark = pytest.mark.achievements

ME, BOB, BOT = 1, 2, 999
BUTTON = i18n.t("achievements.someone_else", "EN")


def _seed(session_factory, *, bob: bool = True) -> None:
    with session_scope(session_factory) as session:
        session.add_all([Player(telegram_user_id=ME, username="me"), Player(telegram_user_id=BOT)])
        if bob:
            session.add(Player(telegram_user_id=BOB, username="bob"))
            session.flush()
            engine.grant(session, engine.GrantRequest(BOB, "clutch"))


def _context(session_factory, args: list[str] | None = None) -> MagicMock:
    context = MagicMock()
    context.args = args or []
    context.bot.id = BOT
    context.bot.get_chat_member = AsyncMock(return_value=MagicMock(status=ChatMemberStatus.MEMBER))
    context.bot_data = {
        "session_factory": session_factory,
        "group_chat_id": 555,
        "game_topic_id": 7,
        "bot_username": "nanibot",
    }
    return context


def _command_update(chat_type: str) -> MagicMock:
    update = MagicMock()
    update.effective_user.id = ME
    private = chat_type == ChatType.PRIVATE
    update.effective_chat.id = ME if private else 555
    update.effective_chat.type = chat_type
    update.message.chat_id = ME if private else 555
    update.message.message_thread_id = None if private else 7
    update.effective_message = update.message
    update.message.reply_text = AsyncMock()
    return update


async def _command(session_factory, args: list[str], chat_type: str) -> AsyncMock:
    """The markdown/markup send_rich got, with the module that sent it patched."""
    module = browser if chat_type == ChatType.PRIVATE else achievements
    with patch.object(module, "send_rich", new=AsyncMock()) as sent:
        await achievements.achievements_command(
            cast(Update, _command_update(chat_type)),
            cast(ContextTypes.DEFAULT_TYPE, _context(session_factory, args)),
        )
    return sent


async def _tap(
    session_factory, data: str, *, user_id: int = ME, chat_type: str = ChatType.SUPERGROUP
) -> tuple[MagicMock, AsyncMock]:
    update = MagicMock()
    query = update.callback_query
    query.data = data
    query.from_user.id = user_id
    message = MagicMock(spec=Message)
    private = chat_type == ChatType.PRIVATE
    message.chat.type = chat_type
    message.chat_id = ME if private else 555
    message.message_thread_id = None if private else 7
    message.reply_text = AsyncMock()
    query.message = message
    query.answer = AsyncMock()
    query.delete_message = AsyncMock()
    module = browser if private else achievements
    with patch.object(module, "send_rich", new=AsyncMock()) as sent:
        await someone_else_picker.achievements_someone_else_callback(
            cast(Update, update), cast(ContextTypes.DEFAULT_TYPE, _context(session_factory))
        )
    return query, sent


def _rows(markup) -> list[list[tuple[str, str | None]]]:
    return [[(b.text, b.callback_data) for b in row] for row in markup.inline_keyboard]


@pytest.mark.parametrize("chat_type", [ChatType.PRIVATE, ChatType.SUPERGROUP])
async def test_your_own_list_gets_someone_else_and_another_players_does_not(
    session_factory, chat_type: str
) -> None:
    _seed(session_factory)

    mine = await _command(session_factory, [], chat_type)
    bobs = await _command(session_factory, ["@bob"], chat_type)

    assert _rows(mine.await_args_list[-1].args[3])[-1] == [(BUTTON, "acs:1:o")]
    assert all(
        data != "acs:1:o" for row in _rows(bobs.await_args_list[-1].args[3]) for _, data in row
    )


async def test_the_button_opens_a_picker_of_everyone_but_you_and_nani_pix(session_factory) -> None:
    _seed(session_factory)

    query, _ = await _tap(session_factory, "acs:1:o")

    query.message.reply_text.assert_awaited_once()
    call = query.message.reply_text.await_args
    assert call.args[0] == i18n.t("achievements.pick_player", "EN")
    assert _rows(call.kwargs["reply_markup"]) == [
        [("@bob", "acs:1:t:2")],
        [(i18n.t("player_picker.cancel", "EN"), "acs:1:x")],
    ]


@pytest.mark.parametrize("chat_type", [ChatType.PRIVATE, ChatType.SUPERGROUP])
async def test_a_pick_shows_that_players_list_like_the_username_form(
    session_factory, chat_type: str
) -> None:
    _seed(session_factory)
    typed = await _command(session_factory, ["@bob"], chat_type)

    query, sent = await _tap(session_factory, "acs:1:t:2", chat_type=chat_type)

    _bot, target, markdown, markup = sent.await_args_list[-1].args
    _tbot, typed_target, typed_markdown, typed_markup = typed.await_args_list[-1].args
    assert (target.chat_id, target.thread_id) == (typed_target.chat_id, typed_target.thread_id)
    assert markdown == typed_markdown
    assert _rows(markup) == _rows(typed_markup)
    query.delete_message.assert_awaited_once()


async def test_someone_elses_tap_gets_the_alert(session_factory) -> None:
    _seed(session_factory)

    query, sent = await _tap(session_factory, "acs:1:t:2", user_id=BOB)

    query.answer.assert_awaited_once_with(i18n.t("player_picker.not_yours", "EN"), show_alert=True)
    sent.assert_not_awaited()


async def test_no_one_else_to_show(session_factory) -> None:
    _seed(session_factory, bob=False)

    query, _ = await _tap(session_factory, "acs:1:o")

    query.message.reply_text.assert_awaited_once_with(
        i18n.t("achievements.no_one", "EN"), reply_markup=None, do_quote=False
    )


async def test_a_faceless_player_is_unmasked_in_the_picker_and_masked_after(
    session_factory, fake_runs
) -> None:
    _seed(session_factory)
    with session_scope(session_factory) as session:
        add_hex(session, BOB, HexKind.NOPPERABO, hours=2)

    opened, _ = await _tap(session_factory, "acs:1:o")
    _, sent = await _tap(session_factory, "acs:1:t:2")

    assert _rows(opened.message.reply_text.await_args.kwargs["reply_markup"])[0][0][0] == "@bob"
    assert r"@bob" not in sent.await_args_list[-1].args[2]
