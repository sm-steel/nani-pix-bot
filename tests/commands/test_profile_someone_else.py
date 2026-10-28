"""🔍 Someone else under your own /profile card: the player picker it opens,
and a pick sending that player's card the way /profile @user does."""

from typing import cast
from unittest.mock import AsyncMock, MagicMock

import pytest
from telegram import Message, Update
from telegram.constants import ChatMemberStatus, ChatType
from telegram.ext import ContextTypes

from nani_pix_bot.commands import profile as profile_module
from nani_pix_bot.commands.profile import card as card_module
from nani_pix_bot.commands.profile import someone_else_picker
from nani_pix_bot.models.game import Game
from nani_pix_bot.models.game_guess import GameGuess
from nani_pix_bot.models.player import Player
from nani_pix_bot.seasons import registry
from nani_pix_bot.seasons.mechanics import HexKind
from nani_pix_bot.services import i18n
from nani_pix_bot.services import players as players_service
from tests.hex_fixtures import add_hex

ME, BOB, CAROL, BOT = 1, 7, 8, 999
CANCEL = i18n.t("player_picker.cancel", "EN")


@pytest.fixture(autouse=True)
def fake_runs(monkeypatch):
    runs = registry.discover("tests.seasons.fake_runs")
    monkeypatch.setattr(registry, "all_runs", lambda: runs)
    registry.all_items.cache_clear()
    yield runs
    registry.all_items.cache_clear()


@pytest.fixture(autouse=True)
def avatar(monkeypatch) -> None:
    monkeypatch.setattr(card_module, "fetch_avatar", AsyncMock(return_value=None))


def _seed(session_factory, others: dict[int, str] | None = None) -> None:
    """me, Bob, Carol and Nani Pix are known; Carol guessed, so she comes first."""
    names = others if others is not None else {BOB: "Bob", CAROL: "carol"}
    with session_factory() as session:
        session.add_all(
            Player(telegram_user_id=pid, username=name)
            for pid, name in {ME: "me", BOT: "the_bot", **names}.items()
        )
        session.flush()
        if CAROL in names:
            session.add(Game(id=1, starter_id=ME))
            session.flush()
            session.add(GameGuess(game_id=1, player_id=CAROL, text="x", stage=1))
        session.commit()


def _context(session_factory, args: list[str] | None = None, *, member: bool = True) -> MagicMock:
    context = MagicMock()
    joined = ChatMemberStatus.MEMBER if member else ChatMemberStatus.LEFT
    context.bot.get_chat_member = AsyncMock(return_value=MagicMock(status=joined))
    context.bot.id = BOT
    context.bot_data = {
        "session_factory": session_factory,
        "group_chat_id": 555,
        "game_topic_id": 7,
    }
    context.args = args or []
    return context


def _message(chat_type: str) -> MagicMock:
    message = MagicMock(spec=Message)
    message.chat.type = chat_type
    message.reply_text = AsyncMock()
    message.reply_photo = AsyncMock()
    return message


async def _tap(
    session_factory,
    data: str,
    *,
    user_id: int = ME,
    chat_type: str = ChatType.SUPERGROUP,
    member: bool = True,
) -> MagicMock:
    update = MagicMock()
    query = update.callback_query
    query.data = data
    query.from_user.id = user_id
    query.message = _message(chat_type)
    query.answer = AsyncMock()
    query.edit_message_text = AsyncMock()
    query.delete_message = AsyncMock()
    await someone_else_picker.profile_someone_else_callback(
        cast(Update, update),
        cast(ContextTypes.DEFAULT_TYPE, _context(session_factory, member=member)),
    )
    return query


def _keyboard(markup) -> list[list[tuple[str, str]]]:
    return [[(b.text, b.callback_data) for b in row] for row in markup.inline_keyboard]


async def _command(session_factory, args: list[str]) -> MagicMock:
    update = MagicMock()
    update.effective_user.id = ME
    update.effective_user.username = "me"
    update.effective_chat.id = 555
    update.effective_chat.type = ChatType.SUPERGROUP
    update.message.message_thread_id = 7
    update.effective_message = update.message
    update.message.reply_text = AsyncMock()
    update.message.reply_photo = AsyncMock()
    await profile_module.profile_command(
        cast(Update, update), cast(ContextTypes.DEFAULT_TYPE, _context(session_factory, args))
    )
    return update


async def test_your_own_card_gets_someone_else_and_another_players_does_not(
    session_factory,
) -> None:
    _seed(session_factory)

    mine = await _command(session_factory, [])
    bobs = await _command(session_factory, ["@bob"])

    own_rows = _keyboard(mine.message.reply_photo.await_args.kwargs["reply_markup"])
    bob_rows = _keyboard(bobs.message.reply_photo.await_args.kwargs["reply_markup"])
    assert own_rows[-1] == [(i18n.t("profile.someone_else", "EN"), "prs:1:o")]
    assert all(not data.startswith("prs:") for row in bob_rows for _, data in row)


async def test_the_button_opens_a_picker_of_everyone_but_you_and_nani_pix(
    session_factory, log_records
) -> None:
    _seed(session_factory)

    query = await _tap(session_factory, "prs:1:o")

    query.message.reply_text.assert_awaited_once()
    call = query.message.reply_text.await_args
    assert call.args[0] == i18n.t("profile.pick_player", "EN")
    assert _keyboard(call.kwargs["reply_markup"]) == [
        [("@carol", "prs:1:t:8")],
        [("@Bob", "prs:1:t:7")],
        [(CANCEL, "prs:1:x")],
    ]
    assert any(r.level == "INFO" and "opened the /profile" in r.message for r in log_records)


async def test_a_pick_sends_that_players_card_like_the_username_form(
    session_factory, log_records
) -> None:
    _seed(session_factory)
    typed = await _command(session_factory, ["@bob"])
    typed_kwargs = typed.message.reply_photo.await_args.kwargs

    query = await _tap(session_factory, "prs:1:t:7")

    query.message.reply_photo.assert_awaited_once()
    kwargs = query.message.reply_photo.await_args.kwargs
    assert kwargs["caption"] == typed_kwargs["caption"]
    assert _keyboard(kwargs["reply_markup"]) == _keyboard(typed_kwargs["reply_markup"])
    assert kwargs["do_quote"] is False
    query.delete_message.assert_awaited_once()
    assert any(
        r.level == "INFO" and "picked" in r.message and r.extra["target_id"] == BOB
        for r in log_records
    )


@pytest.mark.parametrize("data", ["prs:1:o", "prs:1:t:7", "prs:1:p:0", "prs:1:x"])
async def test_someone_elses_tap_gets_the_alert(session_factory, data: str) -> None:
    _seed(session_factory)

    query = await _tap(session_factory, data, user_id=BOB)

    query.answer.assert_awaited_once_with(i18n.t("player_picker.not_yours", "EN"), show_alert=True)
    query.message.reply_text.assert_not_awaited()
    query.message.reply_photo.assert_not_awaited()


@pytest.mark.parametrize("target", [ME, BOT, 12345])
async def test_a_pick_of_yourself_nani_pix_or_a_stranger_is_stale(
    session_factory, target: int
) -> None:
    _seed(session_factory)

    query = await _tap(session_factory, f"prs:1:t:{target}")

    query.answer.assert_awaited_once_with(i18n.t("player_picker.stale", "EN"), show_alert=True)
    query.message.reply_photo.assert_not_awaited()


async def test_a_member_who_left_is_refused_in_dm(session_factory) -> None:
    _seed(session_factory)

    query = await _tap(session_factory, "prs:1:t:7", chat_type=ChatType.PRIVATE, member=False)

    query.answer.assert_awaited_once_with(i18n.t("dm_start.not_a_member", "EN"), show_alert=True)
    query.message.reply_photo.assert_not_awaited()


async def test_the_picker_pages_with_nine_players(session_factory) -> None:
    _seed(session_factory, {pid: f"p{pid}" for pid in range(10, 19)})

    first = await _tap(session_factory, "prs:1:o")
    second = await _tap(session_factory, "prs:1:p:1")

    rows = _keyboard(first.message.reply_text.await_args.kwargs["reply_markup"])
    assert len(rows) == 9
    assert rows[0] == [("@p10", "prs:1:t:10")]
    page = _keyboard(second.edit_message_text.await_args.kwargs["reply_markup"])
    assert page[0] == [("@p18", "prs:1:t:18")]


async def test_no_one_else_to_show(session_factory) -> None:
    _seed(session_factory, {})

    query = await _tap(session_factory, "prs:1:o")

    query.message.reply_text.assert_awaited_once_with(
        i18n.t("profile.no_one", "EN"), reply_markup=None, do_quote=False
    )


async def test_cancel(session_factory) -> None:
    _seed(session_factory)

    query = await _tap(session_factory, "prs:1:x")

    query.edit_message_text.assert_awaited_once_with(
        i18n.t("player_picker.cancelled", "EN"), reply_markup=None
    )


async def test_a_faceless_player_is_unmasked_in_the_picker_and_masked_on_the_card(
    session_factory,
) -> None:
    _seed(session_factory)
    with session_factory() as session:
        add_hex(session, BOB, HexKind.NOPPERABO, hours=2)
        masked = players_service.display_name(session, BOB)
        session.commit()

    opened = await _tap(session_factory, "prs:1:o")
    picked = await _tap(session_factory, "prs:1:t:7")

    rows = _keyboard(opened.message.reply_text.await_args.kwargs["reply_markup"])
    assert [row[0][0] for row in rows[:-1]] == ["@carol", "@Bob"]
    caption = picked.message.reply_photo.await_args.kwargs["caption"]
    assert masked != "@Bob"
    assert "@Bob" not in caption
