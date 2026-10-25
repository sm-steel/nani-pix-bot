"""/correct with no @username: the picker of this round's guessers, and its taps."""

from typing import cast
from unittest.mock import AsyncMock, MagicMock

import pytest
from telegram import Update
from telegram.ext import ContextTypes

from nani_pix_bot.commands import game_flow
from nani_pix_bot.models.enums import GameStatus, PixelStage
from nani_pix_bot.models.game import Game
from nani_pix_bot.models.game_guess import GameGuess
from nani_pix_bot.models.player import Player
from nani_pix_bot.seasons.mechanics import HexKind
from nani_pix_bot.services import i18n
from tests.hex_fixtures import add_hex

STARTER, BOB, CAROL, DAVE, BOT = 1, 2, 3, 4, 99
CANCEL = i18n.t("player_picker.cancel", "EN")


def _seed(session_factory, guessers: tuple[int, ...] = (CAROL, BOB, DAVE)) -> int:
    """An ACTIVE game Starter hosts; `guessers` guessed in that order."""
    with session_factory() as session:
        names = {STARTER: "starter", BOB: "bob", CAROL: "carol", DAVE: "dave", BOT: "the_bot"}
        ids = set(names) | set(guessers)
        session.add_all([Player(telegram_user_id=u, username=names.get(u, f"p{u}")) for u in ids])
        game = Game(
            starter_id=STARTER,
            original_image=b"file123",
            status=GameStatus.ACTIVE,
            current_stage=PixelStage.STAGE_1,
            title_english="Frieren",
            total_guess_count=max(len(guessers), 1),
        )
        session.add(game)
        session.flush()
        session.add_all(
            [GameGuess(game_id=game.id, player_id=u, text="x", stage=1) for u in guessers]
        )
        session.commit()
        return game.id


def _context(session_factory, args: list[str] | None = None) -> MagicMock:
    context = MagicMock()
    context.bot_data = {
        "session_factory": session_factory,
        "group_chat_id": 555,
        "game_topic_id": 7,
        "bot_username": "nani_pix_bot",
    }
    context.args = args or []
    context.bot.id = BOT
    context.job_queue.get_jobs_by_name.return_value = []
    context.bot.send_photo = AsyncMock(return_value=MagicMock(message_id=999))
    context.bot.pin_chat_message = AsyncMock()
    context.bot.unpin_chat_message = AsyncMock()
    context.application.create_task = MagicMock(side_effect=lambda coro, **kw: coro.close())
    return context


async def _run(session_factory, args: list[str] | None = None) -> tuple[MagicMock, MagicMock]:
    update = MagicMock()
    update.effective_user.id = STARTER
    update.effective_chat.id = 555
    update.message.message_thread_id = 7
    update.effective_message = update.message
    update.message.reply_text = AsyncMock()
    context = _context(session_factory, args)
    await game_flow.correct_command(cast(Update, update), cast(ContextTypes.DEFAULT_TYPE, context))
    return update, context


async def _correct(session_factory, args: list[str] | None = None) -> MagicMock:
    return (await _run(session_factory, args))[0]


async def _tap(session_factory, data: str, user_id: int = STARTER) -> tuple[MagicMock, MagicMock]:
    update = MagicMock()
    query = update.callback_query
    query.data = data
    query.from_user.id = user_id
    query.answer = AsyncMock()
    query.edit_message_text = AsyncMock()
    query.delete_message = AsyncMock()
    context = _context(session_factory)
    await game_flow.correct_callback(cast(Update, update), cast(ContextTypes.DEFAULT_TYPE, context))
    return query, context


def _keyboard(markup) -> list[list[tuple[str, str]]]:
    return [[(b.text, b.callback_data) for b in row] for row in markup.inline_keyboard]


def _picker(update: MagicMock) -> tuple[str, list[list[tuple[str, str]]]]:
    update.message.reply_text.assert_awaited_once()
    call = update.message.reply_text.await_args
    return call.args[0], _keyboard(call.kwargs["reply_markup"])


def _game(session_factory, game_id: int) -> tuple[GameStatus, int | None]:
    with session_factory() as session:
        game = session.get(Game, game_id)
        assert game is not None
        return game.status, game.winner_id


async def test_correct_alone_lists_the_guessers_latest_first(session_factory, log_records) -> None:
    game_id = _seed(session_factory)

    text, rows = _picker(await _correct(session_factory))

    assert text == i18n.t("correct.pick_player", "EN")
    assert rows == [
        [("@dave", f"cor:1:{game_id}:t:4")],
        [("@bob", f"cor:1:{game_id}:t:2")],
        [("@carol", f"cor:1:{game_id}:t:3")],
        [(CANCEL, f"cor:1:{game_id}:x")],
    ]
    assert any(r.level == "INFO" and "opened the /correct picker" in r.message for r in log_records)


async def test_a_pick_awards_the_win_like_the_username_form(session_factory) -> None:
    game_id = _seed(session_factory)

    query, context = await _tap(session_factory, f"cor:1:{game_id}:t:2")

    assert _game(session_factory, game_id) == (GameStatus.WON, BOB)
    context.bot.send_photo.assert_awaited_once()
    assert "@bob" in context.bot.send_photo.await_args.kwargs["caption"]
    query.delete_message.assert_awaited_once()


async def test_the_username_form_names_the_winner_the_same_way(session_factory) -> None:
    game_id = _seed(session_factory)

    _, context = await _run(session_factory, ["@bob"])

    assert _game(session_factory, game_id) == (GameStatus.WON, BOB)
    assert "@bob" in context.bot.send_photo.await_args.kwargs["caption"]


@pytest.mark.parametrize("data", ["cor:1:{g}:t:2", "cor:1:{g}:p:0", "cor:1:{g}:x"])
async def test_someone_elses_tap_is_refused(session_factory, data: str) -> None:
    game_id = _seed(session_factory)

    query, context = await _tap(session_factory, data.format(g=game_id), user_id=BOB)

    query.answer.assert_awaited_once_with(i18n.t("player_picker.not_yours", "EN"), show_alert=True)
    assert _game(session_factory, game_id) == (GameStatus.ACTIVE, None)
    context.bot.send_photo.assert_not_awaited()


async def test_the_picker_pages_with_nine_guessers(session_factory) -> None:
    game_id = _seed(session_factory, guessers=tuple(range(10, 19)))

    _, first = _picker(await _correct(session_factory))
    query, _ = await _tap(session_factory, f"cor:1:{game_id}:p:1")
    second = _keyboard(query.edit_message_text.await_args.kwargs["reply_markup"])

    assert len(first) == 9
    assert first[0] == [("@p18", f"cor:1:{game_id}:t:18")]
    assert second[0] == [("@p10", f"cor:1:{game_id}:t:10")]
    assert second[-1][0] == (i18n.t("player_picker.prev", "EN"), f"cor:1:{game_id}:p:0")


async def test_no_one_to_pick(session_factory) -> None:
    _seed(session_factory, guessers=())

    update = await _correct(session_factory)

    assert update.message.reply_text.await_args.args[0] == i18n.t("correct.no_one", "EN")
    assert update.message.reply_text.await_args.kwargs["reply_markup"] is None


async def test_correct_alone_without_a_game_still_says_so(session_factory) -> None:
    update = await _correct(session_factory)

    assert update.message.reply_text.await_args.args[0] == i18n.t("correct.no_game", "EN")


async def test_extra_arguments_get_the_usage(session_factory) -> None:
    _seed(session_factory)

    update = await _correct(session_factory, ["@bob", "now"])

    assert update.message.reply_text.await_args.args[0] == i18n.t("correct.usage", "EN")


@pytest.mark.parametrize("data", ["cor:1:{other}:t:2", "cor:1:{g}:t:1", "cor:1:{g}:t:5"])
async def test_a_stale_pick_is_refused(session_factory, data: str, log_records) -> None:
    game_id = _seed(session_factory)

    query, context = await _tap(session_factory, data.format(g=game_id, other=game_id + 1))

    query.answer.assert_awaited_once_with(i18n.t("player_picker.stale", "EN"), show_alert=True)
    assert _game(session_factory, game_id) == (GameStatus.ACTIVE, None)
    context.bot.send_photo.assert_not_awaited()
    assert any(r.level == "WARNING" and "stale" in r.message for r in log_records)


async def test_a_tap_after_the_game_ended_is_refused(session_factory) -> None:
    game_id = _seed(session_factory)
    await _tap(session_factory, f"cor:1:{game_id}:t:2")

    query, context = await _tap(session_factory, f"cor:1:{game_id}:t:3")

    query.answer.assert_awaited_once_with(i18n.t("correct.no_game", "EN"), show_alert=True)
    assert _game(session_factory, game_id) == (GameStatus.WON, BOB)
    context.bot.send_photo.assert_not_awaited()


async def test_cancel(session_factory) -> None:
    game_id = _seed(session_factory)

    query, _ = await _tap(session_factory, f"cor:1:{game_id}:x")

    query.edit_message_text.assert_awaited_once_with(
        i18n.t("player_picker.cancelled", "EN"), reply_markup=None
    )


async def test_a_faceless_guesser_shows_unmasked_in_the_picker(session_factory, fake_runs) -> None:
    _seed(session_factory)
    with session_factory() as session:
        add_hex(session, BOB, HexKind.NOPPERABO, hours=2)
        session.commit()

    _, rows = _picker(await _correct(session_factory))

    assert [row[0][0] for row in rows[:-1]] == ["@dave", "@bob", "@carol"]
