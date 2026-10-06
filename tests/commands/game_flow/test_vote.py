from typing import cast
from unittest.mock import AsyncMock, MagicMock

from sqlalchemy import select
from telegram import Update
from telegram.ext import ContextTypes

from nani_pix_bot.commands.game_flow import vote as vote_command_module
from nani_pix_bot.models import GameVote
from nani_pix_bot.models.enums import GameStatus
from nani_pix_bot.models.game import Game
from nani_pix_bot.models.game_guess import GameGuess
from nani_pix_bot.models.player import Player
from nani_pix_bot.services import i18n

BOT, A, B, VOTER = 100, 1, 2, 3


def _game(session_factory, status: GameStatus = GameStatus.VOTING) -> int:
    with session_factory() as session:
        session.add_all([Player(telegram_user_id=n) for n in (BOT, A, B, VOTER)])
        session.flush()
        game = Game(starter_id=BOT, status=status, hard_mode=True, hard_mode_turn=2)
        session.add(game)
        session.flush()
        for player in (A, B):
            session.add(
                GameGuess(game_id=game.id, player_id=player, text="g", stage=2, correct=False)
            )
        session.commit()
        return game.id


async def _press(session_factory, user_id: int, game_id: int, candidate_id: int) -> MagicMock:
    query = MagicMock()
    query.data = f"vote:{game_id}:{candidate_id}"
    query.from_user = MagicMock(id=user_id, username=f"u{user_id}")
    query.answer = AsyncMock()
    query.edit_message_text = AsyncMock()
    update = MagicMock()
    update.callback_query = query
    context = MagicMock()
    context.bot_data = {"session_factory": session_factory}
    await vote_command_module.vote_callback_handler(
        cast(Update, update), cast(ContextTypes.DEFAULT_TYPE, context)
    )
    return query


def _stored_votes(session_factory) -> list[tuple[int, int]]:
    with session_factory() as session:
        rows = session.scalars(select(GameVote)).all()
        return [(row.voter_id, row.candidate_id) for row in rows]


async def test_a_tap_stores_the_vote_and_rerenders_the_ballot(session_factory) -> None:
    game_id = _game(session_factory)

    query = await _press(session_factory, VOTER, game_id, A)

    assert _stored_votes(session_factory) == [(VOTER, A)]
    assert query.answer.await_args.args[0] == i18n.t("vote.counted", "en")
    markup = query.edit_message_text.await_args.kwargs["reply_markup"]
    labels = [row[0].text for row in markup.inline_keyboard]
    assert any(label.endswith("— 1") for label in labels)


async def test_voting_for_yourself_is_refused(session_factory) -> None:
    game_id = _game(session_factory)

    query = await _press(session_factory, A, game_id, A)

    assert _stored_votes(session_factory) == []
    assert query.answer.await_args.args[0] == i18n.t("vote.refused.self", "en")


async def test_a_tap_after_the_game_closed_is_refused(session_factory) -> None:
    game_id = _game(session_factory, GameStatus.UNSOLVED)

    query = await _press(session_factory, VOTER, game_id, A)

    assert _stored_votes(session_factory) == []
    assert query.answer.await_args.args[0] == i18n.t("vote.refused.closed", "en")
