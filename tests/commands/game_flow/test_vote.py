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


async def _press(session_factory, user_id: int, game_id: int, candidate_id: int | str) -> MagicMock:
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


async def test_the_ballot_offers_nobody_and_a_tap_on_it_is_stored(session_factory) -> None:
    """Issue #458: the group can say no guess was right."""
    game_id = _game(session_factory)

    query = await _press(session_factory, A, game_id, "none")  # a guesser may pick it too

    assert _stored_votes(session_factory) == [(A, None)]
    markup = query.edit_message_text.await_args.kwargs["reply_markup"]
    last = markup.inline_keyboard[-1][0]
    assert last.callback_data == f"vote:{game_id}:none"
    assert last.text == f"{i18n.t('vote.nobody', 'en')} — 1"


async def test_a_clear_lead_closes_the_vote_at_once(session_factory, monkeypatch) -> None:
    """A and B guessed (2 active, minus the bot host): the bar is 3. The
    third vote for the same option closes the vote without waiting."""
    game_id = _game(session_factory)
    with session_factory() as session:
        session.add_all([Player(telegram_user_id=n) for n in (4, 5)])
        session.commit()
    closed = AsyncMock()
    monkeypatch.setattr(vote_command_module.timeout_module, "close_vote_now", closed)

    await _press(session_factory, VOTER, game_id, "none")
    await _press(session_factory, 4, game_id, "none")
    closed.assert_not_awaited()
    query = await _press(session_factory, 5, game_id, "none")

    closed.assert_awaited_once()
    close_call = closed.await_args
    assert close_call is not None
    assert close_call.args[2] == game_id
    query.edit_message_text.assert_not_awaited()  # the close removes the buttons itself


async def test_split_votes_below_the_bar_keep_the_vote_open(session_factory, monkeypatch) -> None:
    """2-2 between a guesser and Nobody: no option has 3, so it waits.
    (A tie *at* the bar can't arise tap by tap — the first to reach it
    closes the vote — so the tie rule is tested in services.)"""
    game_id = _game(session_factory)
    with session_factory() as session:
        session.add_all([Player(telegram_user_id=n) for n in (4, 5, 6)])
        session.commit()
    closed = AsyncMock()
    monkeypatch.setattr(vote_command_module.timeout_module, "close_vote_now", closed)

    for voter, choice in ((VOTER, A), (4, A), (5, "none"), (6, "none")):
        query = await _press(session_factory, voter, game_id, choice)

    closed.assert_not_awaited()
    query.edit_message_text.assert_awaited_once()  # the ballot is re-rendered


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
