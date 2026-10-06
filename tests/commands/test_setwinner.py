from datetime import UTC, datetime
from typing import cast
from unittest.mock import AsyncMock, MagicMock

from telegram import Update
from telegram.ext import ContextTypes

from nani_pix_bot.commands import setwinner as setwinner_module
from nani_pix_bot.jobs import timers
from nani_pix_bot.models import Player
from nani_pix_bot.models.enums import GameStatus
from nani_pix_bot.models.game import Game
from nani_pix_bot.services import i18n
from nani_pix_bot.services.economy import config
from nani_pix_bot.services.economy.config import EconomyKey
from tests.jobs.test_vote import _job_context, _voting_game

ADMIN, BOT, STARTER, WINNER = 9, 100, 1, 2
START = 100
DEFAULT_AMOUNTS = config.DEFAULT_AMOUNTS


def _seed_unsolved(session_factory, *, status=GameStatus.UNSOLVED) -> int:
    """A bot-started hard-mode game that ended unsolved; @winner is known."""
    with session_factory() as session:
        session.add_all(
            [
                Player(telegram_user_id=BOT),
                Player(telegram_user_id=STARTER, username="starter", currency=START),
                Player(telegram_user_id=WINNER, username="winner", currency=START),
            ]
        )
        session.flush()
        game = Game(
            starter_id=BOT,
            status=status,
            hard_mode=True,
            hard_mode_turn=2,
            title_romaji="Sousou no Frieren",
            ended_at=datetime(2026, 10, 5, 21, 11, tzinfo=UTC),
        )
        session.add(game)
        session.commit()
        return game.id


def _name_player_winner(session_factory, player_id: int) -> None:
    with session_factory() as session:
        player = session.get(Player, player_id)
        assert player is not None
        player.username = "winner"
        session.commit()


def _context(session_factory, args) -> MagicMock:
    context = MagicMock()
    context.bot.id = BOT
    context.bot_data = {
        "session_factory": session_factory,
        "group_chat_id": 555,
        "game_topic_id": 7,
    }
    context.args = args
    context.bot.send_message = AsyncMock()
    context.application.create_task = MagicMock(side_effect=lambda coro, **kw: coro.close())
    return context


def _update() -> MagicMock:
    update = MagicMock()
    update.effective_user.id = ADMIN
    update.effective_chat.type = "private"
    update.message.reply_text = AsyncMock()
    return update


async def _command(monkeypatch, session_factory, args, *, admin=True):
    monkeypatch.setattr(setwinner_module, "is_group_admin", AsyncMock(return_value=admin))
    update, context = _update(), _context(session_factory, args)
    await setwinner_module.setwinner_command(
        cast(Update, update), cast(ContextTypes.DEFAULT_TYPE, context)
    )
    return update, context


def _reply(update) -> str:
    return update.message.reply_text.await_args.args[0]


def _currency(session_factory, player_id: int) -> int:
    with session_factory() as session:
        player = session.get(Player, player_id)
        assert player is not None
        return player.currency


async def test_unsolved_game_is_refinished_and_announced(monkeypatch, session_factory) -> None:
    game_id = _seed_unsolved(session_factory)
    update, context = await _command(monkeypatch, session_factory, [str(game_id), "@winner"])

    with session_factory() as session:
        game = session.get(Game, game_id)
        assert game is not None
        assert game.status is GameStatus.WON
        assert game.winner_id == WINNER
    assert _currency(session_factory, WINNER) == START + 2 * DEFAULT_AMOUNTS[EconomyKey.WIN_STAGE_2]
    sent = context.bot.send_message.await_args.kwargs
    assert f"#{game_id}" in sent["text"]
    assert sent["chat_id"] == 555
    assert sent["message_thread_id"] == 7
    assert _reply(update) == i18n.t("setwinner.done", "en", winner="@winner", game_id=game_id)


async def test_voting_game_is_closed_with_the_named_winner(monkeypatch, session_factory) -> None:
    game_id = _voting_game(session_factory, votes={})
    _name_player_winner(session_factory, 1)

    update, context = await _command(monkeypatch, session_factory, [str(game_id), "@winner"])

    with session_factory() as session:
        game = session.get(Game, game_id)
        assert game is not None
        assert game.status is GameStatus.WON
        assert game.winner_id == 1
    context.application.create_task.assert_called_once()
    assert _reply(update) == i18n.t("setwinner.done", "en", winner="@winner", game_id=game_id)


async def test_late_vote_timer_after_setwinner_is_a_no_op(monkeypatch, session_factory) -> None:
    # Review Focus #2
    game_id = _voting_game(session_factory, votes={})
    _name_player_winner(session_factory, 1)
    await _command(monkeypatch, session_factory, [str(game_id), "@winner"])
    paid = _currency(session_factory, 1)

    job_context = _job_context(session_factory, game_id)
    overthrow = AsyncMock()
    monkeypatch.setattr("nani_pix_bot.jobs.timers.autostart.maybe_overthrow", overthrow)
    await timers.vote_close_job_callback(job_context)

    assert _currency(session_factory, 1) == paid
    job_context.bot.send_message.assert_not_awaited()
    overthrow.assert_not_awaited()


async def test_non_admin_is_refused(monkeypatch, session_factory) -> None:
    game_id = _seed_unsolved(session_factory)
    update, _ = await _command(monkeypatch, session_factory, [str(game_id), "@winner"], admin=False)
    assert _reply(update) == i18n.t("commands.admins_only", "en")
    with session_factory() as session:
        game = session.get(Game, game_id)
        assert game is not None
        assert game.status is GameStatus.UNSOLVED


async def test_bad_args_reply_with_usage(monkeypatch, session_factory) -> None:
    for args in ([], ["12"], ["abc", "@winner"], ["1", "@a", "extra"]):
        update, _ = await _command(monkeypatch, session_factory, args)
        assert _reply(update) == i18n.t("setwinner.usage", "en")


async def test_unknown_game(monkeypatch, session_factory) -> None:
    _seed_unsolved(session_factory)
    update, _ = await _command(monkeypatch, session_factory, ["999", "@winner"])
    assert _reply(update) == i18n.t("setwinner.refused.not_found", "en", game_id=999)


async def test_won_game_is_not_unsolved(monkeypatch, session_factory) -> None:
    game_id = _seed_unsolved(session_factory, status=GameStatus.WON)
    update, _ = await _command(monkeypatch, session_factory, [str(game_id), "@winner"])
    assert _reply(update) == i18n.t("setwinner.refused.not_unsolved", "en", game_id=game_id)


async def test_the_bot_cannot_be_the_winner(monkeypatch, session_factory) -> None:
    game_id = _seed_unsolved(session_factory)
    with session_factory() as session:
        bot = session.get(Player, BOT)
        assert bot is not None
        bot.username = "the_bot"
        session.commit()
    update, _ = await _command(monkeypatch, session_factory, [str(game_id), "@the_bot"])
    assert _reply(update) == i18n.t("setwinner.refused.bot", "en", game_id=game_id)


async def test_unknown_username(monkeypatch, session_factory) -> None:
    game_id = _seed_unsolved(session_factory)
    update, _ = await _command(monkeypatch, session_factory, [str(game_id), "@nobody"])
    assert _reply(update) == i18n.t("setwinner.unknown_user", "en", username="nobody")
