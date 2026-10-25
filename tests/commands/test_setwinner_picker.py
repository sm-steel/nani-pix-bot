"""/setwinner <game id> with no @username: the menu of that game's guessers,
and its taps."""

from datetime import UTC, datetime
from typing import cast
from unittest.mock import AsyncMock, MagicMock

import pytest
from telegram import Update
from telegram.ext import ContextTypes

from nani_pix_bot.commands import setwinner as setwinner_module
from nani_pix_bot.commands import setwinner_picker
from nani_pix_bot.models import Player
from nani_pix_bot.models.enums import GameStatus, PixelStage
from nani_pix_bot.models.game import Game
from nani_pix_bot.models.game_guess import GameGuess
from nani_pix_bot.models.used_menu import UsedMenu
from nani_pix_bot.seasons.mechanics import HexKind
from nani_pix_bot.services import i18n
from tests.hex_fixtures import add_hex
from tests.jobs.test_vote import _voting_game

ADMIN, HOST, BOB, CAROL, DAVE, BOT = 9, 5, 2, 3, 4, 100
CANCEL = i18n.t("player_picker.cancel", "EN")
DM, MENU_ID = 9, 777


def _seed(
    session_factory,
    guessers: tuple[int, ...] = (CAROL, BOB, DAVE),
    status: GameStatus = GameStatus.UNSOLVED,
) -> int:
    """A game HOST started that ended `status`; `guessers` guessed in that order."""
    names = {HOST: "host", BOB: "bob", CAROL: "carol", DAVE: "dave", BOT: "the_bot"}
    with session_factory() as session:
        ids = set(names) | set(guessers)
        session.add_all([Player(telegram_user_id=u, username=names.get(u, f"p{u}")) for u in ids])
        session.flush()
        game = Game(
            starter_id=HOST,
            status=status,
            current_stage=PixelStage.STAGE_5,
            title_romaji="Sousou no Frieren",
            ended_at=datetime(2026, 10, 5, 21, 11, tzinfo=UTC),
        )
        session.add(game)
        session.flush()
        session.add_all(
            [GameGuess(game_id=game.id, player_id=u, text="x", stage=5) for u in guessers]
        )
        session.commit()
        return game.id


def _context(session_factory, args: list[str] | None = None) -> MagicMock:
    context = MagicMock()
    context.bot.id = BOT
    context.bot_data = {
        "session_factory": session_factory,
        "group_chat_id": 555,
        "game_topic_id": 7,
    }
    context.args = args or []
    context.bot.send_message = AsyncMock()
    context.application.create_task = MagicMock(side_effect=lambda coro, **kw: coro.close())
    return context


async def _command(monkeypatch, session_factory, args: list[str]) -> MagicMock:
    monkeypatch.setattr(setwinner_module, "is_group_admin", AsyncMock(return_value=True))
    update = MagicMock()
    update.effective_user.id = ADMIN
    update.effective_chat.type = "private"
    update.message.reply_text = AsyncMock()
    await setwinner_module.setwinner_command(
        cast(Update, update), cast(ContextTypes.DEFAULT_TYPE, _context(session_factory, args))
    )
    return update


async def _tap(
    monkeypatch, session_factory, data: str, *, user_id: int = ADMIN, admin: bool = True
) -> tuple[MagicMock, MagicMock]:
    monkeypatch.setattr(setwinner_picker, "is_group_admin", AsyncMock(return_value=admin))
    update = MagicMock()
    query = update.callback_query
    query.data = data
    query.from_user.id = user_id
    query.message.chat.id = DM
    query.message.message_id = MENU_ID
    query.answer = AsyncMock()
    query.edit_message_text = AsyncMock()
    context = _context(session_factory)
    await setwinner_picker.setwinner_callback(
        cast(Update, update), cast(ContextTypes.DEFAULT_TYPE, context)
    )
    return query, context


def _keyboard(markup) -> list[list[tuple[str, str]]]:
    return [[(b.text, b.callback_data) for b in row] for row in markup.inline_keyboard]


def _menu(update: MagicMock) -> tuple[str, list[list[tuple[str, str]]]]:
    update.message.reply_text.assert_awaited_once()
    call = update.message.reply_text.await_args
    return call.args[0], _keyboard(call.kwargs["reply_markup"])


def _game(session_factory, game_id: int) -> tuple[GameStatus, int | None]:
    with session_factory() as session:
        game = session.get(Game, game_id)
        assert game is not None
        return game.status, game.winner_id


@pytest.mark.parametrize("prefix", ["", "#"])
async def test_setwinner_with_a_game_id_lists_its_guessers_latest_first(
    monkeypatch, session_factory, prefix: str, log_records
) -> None:
    game_id = _seed(session_factory)

    text, rows = _menu(await _command(monkeypatch, session_factory, [f"{prefix}{game_id}"]))

    assert text == i18n.t("setwinner.pick_player", "EN", game_id=game_id)
    assert rows == [
        [("@dave", f"sw:9:{game_id}:t:4")],
        [("@bob", f"sw:9:{game_id}:t:2")],
        [("@carol", f"sw:9:{game_id}:t:3")],
        [(CANCEL, f"sw:9:{game_id}:x")],
    ]
    assert any(
        r.level == "INFO" and "opened the /setwinner picker" in r.message for r in log_records
    )


@pytest.mark.parametrize(
    ("status", "key"),
    [(GameStatus.WON, "setwinner.refused.not_unsolved"), (None, "setwinner.refused.not_found")],
)
async def test_the_menu_is_refused_for_a_game_that_cant_be_refinished(
    monkeypatch, session_factory, status: GameStatus | None, key: str
) -> None:
    game_id = _seed(session_factory, status=status or GameStatus.UNSOLVED)
    asked = game_id if status is not None else game_id + 1

    update = await _command(monkeypatch, session_factory, [str(asked)])

    update.message.reply_text.assert_awaited_once_with(
        i18n.t(key, "EN", game_id=asked), reply_markup=None
    )


async def test_a_pick_refinishes_like_the_username_form(monkeypatch, session_factory) -> None:
    game_id = _seed(session_factory)

    query, context = await _tap(monkeypatch, session_factory, f"sw:9:{game_id}:t:2")

    assert _game(session_factory, game_id) == (GameStatus.WON, BOB)
    context.bot.send_message.assert_awaited_once()
    assert "@bob" in context.bot.send_message.await_args.kwargs["text"]
    query.edit_message_text.assert_awaited_once_with(
        i18n.t("setwinner.done", "EN", winner="@bob", game_id=game_id), reply_markup=None
    )


async def test_the_username_form_replies_the_same_way(monkeypatch, session_factory) -> None:
    game_id = _seed(session_factory)

    update = await _command(monkeypatch, session_factory, [str(game_id), "@bob"])

    assert _game(session_factory, game_id) == (GameStatus.WON, BOB)
    update.message.reply_text.assert_awaited_once_with(
        i18n.t("setwinner.done", "EN", winner="@bob", game_id=game_id)
    )


async def test_a_pick_closes_a_vote(monkeypatch, session_factory) -> None:
    game_id = _voting_game(session_factory, votes={})
    with session_factory() as session:
        player = session.get(Player, 2)
        assert player is not None
        player.username = "bob"
        session.commit()

    query, context = await _tap(monkeypatch, session_factory, f"sw:9:{game_id}:t:2")

    assert _game(session_factory, game_id) == (GameStatus.WON, 2)
    context.application.create_task.assert_called_once()
    query.edit_message_text.assert_awaited_once_with(
        i18n.t("setwinner.done", "EN", winner="@bob", game_id=game_id), reply_markup=None
    )


async def test_a_pick_rechecks_admin_rights(monkeypatch, session_factory, log_records) -> None:
    game_id = _seed(session_factory)

    query, context = await _tap(monkeypatch, session_factory, f"sw:9:{game_id}:t:2", admin=False)

    query.answer.assert_awaited_once_with(i18n.t("commands.admins_only", "EN"), show_alert=True)
    assert _game(session_factory, game_id) == (GameStatus.UNSOLVED, None)
    context.bot.send_message.assert_not_awaited()
    assert any(r.level == "WARNING" and "no longer an admin" in r.message for r in log_records)


@pytest.mark.parametrize("data", ["sw:9:{g}:t:2", "sw:9:{g}:p:0", "sw:9:{g}:x"])
async def test_someone_elses_tap_is_refused(monkeypatch, session_factory, data: str) -> None:
    game_id = _seed(session_factory)

    query, _ = await _tap(monkeypatch, session_factory, data.format(g=game_id), user_id=BOB)

    query.answer.assert_awaited_once_with(i18n.t("player_picker.not_yours", "EN"), show_alert=True)
    assert _game(session_factory, game_id) == (GameStatus.UNSOLVED, None)


async def test_a_pick_after_the_game_was_refinished_is_refused(
    monkeypatch, session_factory
) -> None:
    game_id = _seed(session_factory)
    await _tap(monkeypatch, session_factory, f"sw:9:{game_id}:t:2")

    query, context = await _tap(monkeypatch, session_factory, f"sw:9:{game_id}:t:3")

    query.answer.assert_awaited_once_with(
        i18n.t("setwinner.refused.not_unsolved", "EN", game_id=game_id), show_alert=True
    )
    assert _game(session_factory, game_id) == (GameStatus.WON, BOB)
    context.bot.send_message.assert_not_awaited()


async def test_a_used_menu_acts_only_once(monkeypatch, session_factory, log_records) -> None:
    game_id = _seed(session_factory)
    with session_factory() as session:
        session.add(UsedMenu(chat_id=DM, message_id=MENU_ID))
        session.commit()

    query, _ = await _tap(monkeypatch, session_factory, f"sw:9:{game_id}:t:2")

    query.answer.assert_awaited_once_with(i18n.t("player_picker.stale", "EN"), show_alert=True)
    assert _game(session_factory, game_id) == (GameStatus.UNSOLVED, None)
    assert any(r.level == "WARNING" and "used /setwinner menu" in r.message for r in log_records)


@pytest.mark.parametrize("target", [7, HOST])
async def test_a_pick_of_a_non_guesser_is_refused(monkeypatch, session_factory, target) -> None:
    game_id = _seed(session_factory)

    query, _ = await _tap(monkeypatch, session_factory, f"sw:9:{game_id}:t:{target}")

    query.answer.assert_awaited_once()
    assert query.answer.await_args.kwargs == {"show_alert": True}
    assert _game(session_factory, game_id) == (GameStatus.UNSOLVED, None)


async def test_the_menu_pages_with_nine_guessers(monkeypatch, session_factory) -> None:
    game_id = _seed(session_factory, guessers=tuple(range(10, 19)))

    _, first = _menu(await _command(monkeypatch, session_factory, [str(game_id)]))
    query, _ = await _tap(monkeypatch, session_factory, f"sw:9:{game_id}:p:1")
    second = _keyboard(query.edit_message_text.await_args.kwargs["reply_markup"])

    assert len(first) == 9
    assert first[0] == [("@p18", f"sw:9:{game_id}:t:18")]
    assert second[0] == [("@p10", f"sw:9:{game_id}:t:10")]
    assert second[-1][0] == (i18n.t("player_picker.prev", "EN"), f"sw:9:{game_id}:p:0")


async def test_no_one_to_pick(monkeypatch, session_factory) -> None:
    game_id = _seed(session_factory, guessers=())

    update = await _command(monkeypatch, session_factory, [str(game_id)])

    update.message.reply_text.assert_awaited_once_with(
        i18n.t("setwinner.no_one", "EN", game_id=game_id), reply_markup=None
    )


async def test_cancel(monkeypatch, session_factory) -> None:
    game_id = _seed(session_factory)

    query, _ = await _tap(monkeypatch, session_factory, f"sw:9:{game_id}:x")

    query.edit_message_text.assert_awaited_once_with(
        i18n.t("player_picker.cancelled", "EN"), reply_markup=None
    )


async def test_a_faceless_guesser_shows_unmasked_in_the_menu(
    monkeypatch, session_factory, fake_runs
) -> None:
    game_id = _seed(session_factory)
    with session_factory() as session:
        add_hex(session, BOB, HexKind.NOPPERABO, hours=2)
        session.commit()

    _, rows = _menu(await _command(monkeypatch, session_factory, [str(game_id)]))

    assert [row[0][0] for row in rows[:-1]] == ["@dave", "@bob", "@carol"]
