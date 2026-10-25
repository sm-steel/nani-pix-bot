"""/skip with no @username: the menu (open the turn to everyone, or hand it to
a player), and its taps."""

from typing import cast
from unittest.mock import AsyncMock, MagicMock

import pytest
from telegram import Update
from telegram.ext import ContextTypes

from nani_pix_bot.commands import game_flow
from nani_pix_bot.jobs import timers
from nani_pix_bot.models.enums import GameStatus
from nani_pix_bot.models.game import Game
from nani_pix_bot.models.game_guess import GameGuess
from nani_pix_bot.models.player import Player
from nani_pix_bot.models.turn_state import TurnState
from nani_pix_bot.seasons.mechanics import HexKind
from nani_pix_bot.services import i18n
from tests.hex_fixtures import add_hex

ME, BOB, CAROL, BOT = 1, 2, 3, 99
CANCEL = i18n.t("player_picker.cancel", "EN")
OPEN = i18n.t("skip.open_button", "EN")
CHAT, MENU_ID = 555, 4242
DEFAULT_OTHERS = {BOB: "bob", CAROL: "carol"}


def _seed(session_factory, others: dict[int, str] | None = None, holder: int = ME) -> None:
    """`holder` holds the turn; everyone in `others` (and Nani Pix) is known."""
    names = {ME: "me", BOT: "the_bot", **(DEFAULT_OTHERS if others is None else others)}
    with session_factory() as session:
        session.add_all([Player(telegram_user_id=u, username=n) for u, n in names.items()])
        session.add(TurnState(id=1, next_starter_id=holder))
        session.commit()


def _context(session_factory, args: list[str] | None = None) -> MagicMock:
    context = MagicMock()
    context.bot_data = {
        "session_factory": session_factory,
        "group_chat_id": CHAT,
        "game_topic_id": 7,
        "bot_username": "nani_pix_bot",
    }
    context.args = args or []
    context.bot.id = BOT
    context.job_queue.get_jobs_by_name.return_value = []
    return context


async def _skip(session_factory, args: list[str] | None = None) -> MagicMock:
    update = MagicMock()
    update.effective_user.id = ME
    update.effective_chat.id = CHAT
    update.message.message_thread_id = 7
    update.effective_message = update.message
    update.message.reply_text = AsyncMock()
    context = _context(session_factory, args)
    await game_flow.skip_command(cast(Update, update), cast(ContextTypes.DEFAULT_TYPE, context))
    return update


async def _tap(session_factory, data: str, user_id: int = ME) -> tuple[MagicMock, MagicMock]:
    update = MagicMock()
    query = update.callback_query
    query.data = data
    query.from_user.id = user_id
    query.message.chat.id = CHAT
    query.message.message_id = MENU_ID
    query.answer = AsyncMock()
    query.edit_message_text = AsyncMock()
    context = _context(session_factory)
    await game_flow.skip_callback(cast(Update, update), cast(ContextTypes.DEFAULT_TYPE, context))
    return query, context


def _keyboard(markup) -> list[list[tuple[str, str]]]:
    return [[(b.text, b.callback_data) for b in row] for row in markup.inline_keyboard]


def _menu(update: MagicMock) -> tuple[str, list[list[tuple[str, str]]]]:
    update.message.reply_text.assert_awaited_once()
    call = update.message.reply_text.await_args
    return call.args[0], _keyboard(call.kwargs["reply_markup"])


def _holder(session_factory) -> int | None:
    with session_factory() as session:
        turn_state = session.get(TurnState, 1)
        assert turn_state is not None
        return turn_state.next_starter_id


def _set_holder(session_factory, holder: int | None) -> None:
    with session_factory() as session:
        turn_state = session.get(TurnState, 1)
        assert turn_state is not None
        turn_state.next_starter_id = holder
        session.commit()


def _edited(query: MagicMock) -> str:
    query.edit_message_text.assert_awaited_once()
    return query.edit_message_text.await_args.args[0]


async def test_skip_alone_shows_the_menu(session_factory, log_records) -> None:
    _seed(session_factory)

    text, rows = _menu(await _skip(session_factory))

    assert text == i18n.t("skip.pick_player", "EN")
    assert rows == [
        [(OPEN, "skp:1:o")],
        [("@bob", "skp:1:t:2")],
        [("@carol", "skp:1:t:3")],
        [(CANCEL, "skp:1:x")],
    ]
    assert _holder(session_factory) == ME
    assert any(r.level == "INFO" and "opened the /skip menu" in r.message for r in log_records)


async def test_the_menu_lists_the_most_active_first(session_factory) -> None:
    _seed(session_factory)
    with session_factory() as session:
        game = Game(starter_id=ME, original_image=b"f", status=GameStatus.WON)
        session.add(game)
        session.flush()
        session.add(GameGuess(game_id=game.id, player_id=CAROL, text="x", stage=1))
        session.commit()

    _, rows = _menu(await _skip(session_factory))

    assert [row[0][0] for row in rows[1:-1]] == ["@carol", "@bob"]


async def test_skip_alone_still_refuses_out_of_turn(session_factory) -> None:
    _seed(session_factory, holder=BOB)

    update = await _skip(session_factory)

    update.message.reply_text.assert_awaited_once_with(i18n.t("skip.not_your_turn", "EN"))


async def test_open_to_everyone_does_what_skip_alone_did(session_factory) -> None:
    _seed(session_factory)

    query, context = await _tap(session_factory, "skp:1:o")

    assert _holder(session_factory) is None
    assert _edited(query) == i18n.t("skip.opened", "EN")
    looked_up = [call.args[0] for call in context.job_queue.get_jobs_by_name.call_args_list]
    assert looked_up.count(timers.TURN_REMINDER_JOB_NAME) == 1
    assert looked_up.count(timers.TURN_EXPIRY_JOB_NAME) == 1
    scheduled = [call.kwargs["name"] for call in context.job_queue.run_once.call_args_list]
    assert timers.IDLE_AUTOSTART_JOB_NAME in scheduled


async def test_a_pick_hands_the_turn_like_the_username_form(session_factory) -> None:
    _seed(session_factory)

    query, context = await _tap(session_factory, "skp:1:t:2")

    assert _holder(session_factory) == BOB
    assert _edited(query) == i18n.t("skip.passed", "EN", username="@bob")
    scheduled = [call.kwargs["name"] for call in context.job_queue.run_once.call_args_list]
    assert timers.TURN_REMINDER_JOB_NAME in scheduled
    assert timers.TURN_EXPIRY_JOB_NAME in scheduled


async def test_the_username_form_replies_the_same_way(session_factory) -> None:
    _seed(session_factory)

    update = await _skip(session_factory, ["@bob"])

    assert _holder(session_factory) == BOB
    update.message.reply_text.assert_awaited_once_with(i18n.t("skip.passed", "EN", username="@bob"))


@pytest.mark.parametrize("data", ["skp:1:o", "skp:1:t:2", "skp:1:p:0", "skp:1:x"])
async def test_someone_elses_tap_is_refused(session_factory, data: str) -> None:
    _seed(session_factory)

    query, _ = await _tap(session_factory, data, user_id=BOB)

    query.answer.assert_awaited_once_with(i18n.t("player_picker.not_yours", "EN"), show_alert=True)
    assert _holder(session_factory) == ME
    query.edit_message_text.assert_not_awaited()


@pytest.mark.parametrize("holder", [BOB, None])
@pytest.mark.parametrize("data", ["skp:1:o", "skp:1:t:3"])
async def test_a_tap_after_the_turn_changed_is_stale(
    session_factory, data: str, holder: int | None, log_records
) -> None:
    _seed(session_factory)
    _set_holder(session_factory, holder)

    query, _ = await _tap(session_factory, data)

    query.answer.assert_awaited_once_with(i18n.t("player_picker.stale", "EN"), show_alert=True)
    assert _holder(session_factory) == holder
    query.edit_message_text.assert_not_awaited()
    assert any(r.level == "WARNING" and "stale" in r.message for r in log_records)


async def test_a_used_menu_acts_only_once(session_factory, log_records) -> None:
    _seed(session_factory)
    await _tap(session_factory, "skp:1:o")
    _set_holder(session_factory, ME)  # the turn came back to me since

    query, _ = await _tap(session_factory, "skp:1:t:2")

    query.answer.assert_awaited_once_with(i18n.t("player_picker.stale", "EN"), show_alert=True)
    assert _holder(session_factory) == ME
    assert any(r.level == "WARNING" and "used /skip menu" in r.message for r in log_records)


@pytest.mark.parametrize("target", [BOT, ME, 5])
async def test_a_pick_skip_would_refuse_is_stale(session_factory, target: int) -> None:
    _seed(session_factory)

    query, _ = await _tap(session_factory, f"skp:1:t:{target}")

    query.answer.assert_awaited_once_with(i18n.t("player_picker.stale", "EN"), show_alert=True)
    assert _holder(session_factory) == ME


async def test_the_menu_pages_with_nine_players(session_factory) -> None:
    _seed(session_factory, others={uid: f"p{uid}" for uid in range(10, 19)})

    _, first = _menu(await _skip(session_factory))
    query, _ = await _tap(session_factory, "skp:1:p:1")
    second = _keyboard(query.edit_message_text.await_args.kwargs["reply_markup"])

    assert len(first) == 10
    assert first[0] == [(OPEN, "skp:1:o")]
    assert first[1] == [("@p10", "skp:1:t:10")]
    assert second == [
        [(OPEN, "skp:1:o")],
        [("@p18", "skp:1:t:18")],
        [(i18n.t("player_picker.prev", "EN"), "skp:1:p:0"), (CANCEL, "skp:1:x")],
    ]


async def test_with_no_one_else_the_menu_can_still_open_the_turn(session_factory) -> None:
    _seed(session_factory, others={})

    _, rows = _menu(await _skip(session_factory))

    assert rows == [[(OPEN, "skp:1:o")], [(CANCEL, "skp:1:x")]]


async def test_cancel(session_factory) -> None:
    _seed(session_factory)

    query, _ = await _tap(session_factory, "skp:1:x")

    query.edit_message_text.assert_awaited_once_with(
        i18n.t("player_picker.cancelled", "EN"), reply_markup=None
    )
    assert _holder(session_factory) == ME


async def test_a_faceless_player_shows_unmasked_in_the_menu(session_factory, fake_runs) -> None:
    _seed(session_factory)
    with session_factory() as session:
        add_hex(session, BOB, HexKind.NOPPERABO, hours=2)
        session.commit()

    _, rows = _menu(await _skip(session_factory))

    assert [row[0][0] for row in rows[1:-1]] == ["@bob", "@carol"]
