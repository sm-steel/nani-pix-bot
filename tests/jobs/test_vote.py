from datetime import UTC, datetime, timedelta
from typing import cast
from unittest.mock import AsyncMock, MagicMock

import pytest
from telegram.error import TelegramError
from telegram.ext import ContextTypes

from nani_pix_bot.jobs import timers
from nani_pix_bot.jobs.timers import vote as vote_module
from nani_pix_bot.models import GameVote
from nani_pix_bot.models.enums import GameStatus
from nani_pix_bot.models.game import Game
from nani_pix_bot.models.game_guess import GameGuess
from nani_pix_bot.models.player import Player
from nani_pix_bot.services import game as game_service
from nani_pix_bot.services import i18n
from nani_pix_bot.services.economy import bounty
from tests.services.economy.ledger import ledger_balance

START = 100
BOT, A, B = 100, 1, 2


def _voting_game(
    session_factory, *, votes: dict[int, int], pot: dict[int, int] | None = None, deadline=None
) -> int:
    with session_factory() as session:
        session.add(Player(telegram_user_id=BOT))
        session.add_all([Player(telegram_user_id=n, currency=START) for n in (A, B, 3, 4, 5)])
        session.flush()
        game = Game(
            starter_id=BOT,
            status=GameStatus.ACTIVE,
            hard_mode=True,
            hard_mode_turn=2,
            hard_mode_image_a=b"a",
            hard_mode_image_b=b"b",
            title_romaji="Sousou no Frieren",
            title_english="Frieren",
        )
        session.add(game)
        session.flush()
        for contributor, amount in (pot or {}).items():
            funder = session.get(Player, contributor)
            assert funder is not None
            bounty.contribute(session, game, funder, amount)
        game.status = GameStatus.VOTING
        game.vote_deadline_at = deadline or datetime.now(UTC)
        for player in (A, B):
            session.add(
                GameGuess(game_id=game.id, player_id=player, text="g", stage=2, correct=False)
            )
        for voter, candidate in votes.items():
            session.add(GameVote(game_id=game.id, voter_id=voter, candidate_id=candidate))
        session.commit()
        return game.id


def _job_context(session_factory, game_id: int) -> MagicMock:
    context = MagicMock()
    context.job.data = game_id
    context.bot_data = {
        "session_factory": session_factory,
        "group_chat_id": 555,
        "game_topic_id": 7,
    }
    context.bot.send_message = AsyncMock(return_value=MagicMock(message_id=77))
    context.bot.edit_message_reply_markup = AsyncMock()
    context.bot.send_media_group = AsyncMock(return_value=[MagicMock(message_id=1)])
    context.bot.pin_chat_message = AsyncMock()
    context.bot.unpin_chat_message = AsyncMock()
    context.job_queue.get_jobs_by_name.return_value = []
    return context


def _currency(session_factory, player_id: int) -> int:
    with session_factory() as session:
        player = session.get(Player, player_id)
        assert player is not None
        return player.currency


async def test_close_with_three_votes_pays_the_winner_and_hands_them_the_turn(
    session_factory, monkeypatch: pytest.MonkeyPatch
) -> None:
    game_id = _voting_game(session_factory, votes={3: A, 4: A, 5: A})
    overthrow = AsyncMock()
    monkeypatch.setattr("nani_pix_bot.jobs.timers.autostart.maybe_overthrow", overthrow)
    context = _job_context(session_factory, game_id)

    await vote_module.vote_close_job_callback(context)

    with session_factory() as session:
        game = session.get(Game, game_id)
        assert game is not None
        assert game.status is GameStatus.WON
        assert game.winner_id == A
        assert game.hard_mode_image_a is None  # cleared after the result post
        winner = session.get(Player, A)
        assert winner is not None
        assert winner.currency > START  # the hard-mode win reward was paid
        assert ledger_balance(session, A) == winner.currency - START
        turn_state = game_service.get_turn_state(session)
        assert turn_state is not None
        assert turn_state.next_starter_id == A
    assert "🎉" in context.bot.send_message.await_args.kwargs["text"]
    assert overthrow.await_args is not None
    assert overthrow.await_args.kwargs["winner_id"] == A


async def test_close_without_enough_votes_refunds_the_pot_and_opens_the_turn(
    session_factory, monkeypatch: pytest.MonkeyPatch
) -> None:
    game_id = _voting_game(session_factory, votes={3: A}, pot={4: 50})
    monkeypatch.setattr("nani_pix_bot.jobs.timers.autostart.maybe_overthrow", AsyncMock())

    await vote_module.vote_close_job_callback(_job_context(session_factory, game_id))

    with session_factory() as session:
        game = session.get(Game, game_id)
        assert game is not None
        assert game.status is GameStatus.UNSOLVED
        assert bounty.pot_balance(session, game_id) == 0
        turn_state = game_service.get_turn_state(session)
        assert turn_state is not None
        assert turn_state.next_starter_id is None
    assert _currency(session_factory, 4) == START


async def test_a_late_close_on_an_already_settled_game_is_a_no_op(
    session_factory, monkeypatch: pytest.MonkeyPatch
) -> None:
    game_id = _voting_game(session_factory, votes={3: A, 4: A, 5: A})
    overthrow = AsyncMock()
    monkeypatch.setattr("nani_pix_bot.jobs.timers.autostart.maybe_overthrow", overthrow)
    await vote_module.vote_close_job_callback(_job_context(session_factory, game_id))
    before = _currency(session_factory, A)

    await vote_module.vote_close_job_callback(_job_context(session_factory, game_id))

    assert _currency(session_factory, A) == before
    assert overthrow.await_count == 1


async def test_post_vote_ballot_stores_the_message_id(session_factory) -> None:
    game_id = _voting_game(session_factory, votes={})
    context = _job_context(session_factory, game_id)

    await vote_module.post_vote_ballot(
        cast(ContextTypes.DEFAULT_TYPE, context), session_factory, game_id
    )

    with session_factory() as session:
        game = session.get(Game, game_id)
        assert game is not None
        assert game.vote_message_id == 77
        assert game.hard_mode_image_a is not None  # kept while the vote runs


def test_schedule_vote_close_names_the_job_after_the_game(session_factory) -> None:
    game_id = _voting_game(session_factory, votes={})
    queue = MagicMock()
    queue.get_jobs_by_name.return_value = []
    with session_factory() as session:
        game = session.get(Game, game_id)
        assert game is not None
        vote_module.schedule_vote_close(queue, game)
    assert queue.run_once.call_args.kwargs["name"] == f"vote-close-{game_id}"


async def test_rearm_schedules_an_open_vote_even_if_overdue(session_factory) -> None:
    game_id = _voting_game(
        session_factory, votes={}, deadline=datetime.now(UTC) - timedelta(minutes=5)
    )
    job_queue = MagicMock()
    job_queue.get_jobs_by_name.return_value = []

    await timers.rearm_pending_timeouts(job_queue, session_factory)

    call = next(
        c for c in job_queue.run_once.call_args_list if c.kwargs["name"] == f"vote-close-{game_id}"
    )
    assert call.kwargs["when"] == 0  # seconds_until clamps an overdue deadline to now


def _title(session_factory, game_id: int) -> str:
    with session_factory() as session:
        game = session.get(Game, game_id)
        assert game is not None
        return game_service.display_title(game, "en")


async def test_a_vote_won_by_the_group_says_the_group_decided(
    session_factory, monkeypatch: pytest.MonkeyPatch
) -> None:
    game_id = _voting_game(session_factory, votes={3: A, 4: A, 5: A})
    context = _job_context(session_factory, game_id)
    title = _title(session_factory, game_id)

    await vote_module.finalize_vote(context, session_factory, game_id)

    text = context.bot.send_message.await_args.kwargs["text"]
    assert text.startswith(i18n.t("vote.won", "en", winner=str(A), title=title))


async def test_a_vote_closed_by_setwinner_says_an_admin_decided(
    session_factory, monkeypatch: pytest.MonkeyPatch
) -> None:
    game_id = _voting_game(session_factory, votes={})
    context = _job_context(session_factory, game_id)
    title = _title(session_factory, game_id)

    await vote_module.finalize_vote(context, session_factory, game_id, forced_winner_id=B)

    text = context.bot.send_message.await_args.kwargs["text"]
    assert text.startswith(i18n.t("vote.admin_won", "en", winner=str(B), title=title))
    assert not text.startswith(i18n.t("vote.won", "en", winner=str(B), title=title))


async def test_a_failed_result_post_logs_an_error(session_factory, log_records) -> None:
    game_id = _voting_game(session_factory, votes={3: A, 4: A, 5: A})
    context = _job_context(session_factory, game_id)
    context.bot.send_message = AsyncMock(side_effect=TelegramError("boom"))

    await vote_module.finalize_vote(context, session_factory, game_id)

    failed = [r for r in log_records if r.message.startswith("failed to post the vote result")]
    assert [r.level for r in failed] == ["ERROR"]
