from typing import cast
from unittest.mock import MagicMock

from telegram.ext import ContextTypes

from nani_pix_bot.jobs.timers.mal_link_expiry import (
    MAL_LINK_EXPIRY_DELAY,
    mal_link_expiry_job_callback,
    mal_link_expiry_job_name,
    schedule_mal_link_expiry,
)
from nani_pix_bot.models.player import Player
from nani_pix_bot.services import mal_link


def test_schedule_mal_link_expiry_calls_run_once_with_the_players_job_name() -> None:
    job_queue = MagicMock()

    schedule_mal_link_expiry(job_queue, telegram_user_id=42)

    job_queue.run_once.assert_called_once()
    _, kwargs = job_queue.run_once.call_args
    assert kwargs["name"] == mal_link_expiry_job_name(42)
    assert kwargs["when"] == MAL_LINK_EXPIRY_DELAY
    assert kwargs["data"] == 42


def test_schedule_mal_link_expiry_is_a_noop_when_job_queue_is_none() -> None:
    schedule_mal_link_expiry(None, telegram_user_id=42)  # should not raise


def _make_job_context(session_factory, *, telegram_user_id: int) -> MagicMock:
    context = MagicMock()
    context.job.data = telegram_user_id
    context.bot_data = {"session_factory": session_factory}
    return context


async def test_mal_link_expiry_job_callback_deletes_a_still_pending_row(session_factory) -> None:
    with session_factory() as session:
        session.add(Player(telegram_user_id=42))
        mal_link.upsert_pending_link(session, 42, state="s", code_verifier="v")
        session.commit()
    context = _make_job_context(session_factory, telegram_user_id=42)

    await mal_link_expiry_job_callback(cast(ContextTypes.DEFAULT_TYPE, context))

    with session_factory() as session:
        assert mal_link.get_pending_link(session, 42) is None


async def test_mal_link_expiry_job_callback_is_a_noop_if_already_completed(
    session_factory,
) -> None:
    # The player finished linking (or never started) before the timer
    # fired — the pending row is already gone (e.g. deleted by the
    # code-exchange handler), so this callback finding nothing to
    # delete is the expected, common case, not an error.
    context = _make_job_context(session_factory, telegram_user_id=999)

    await mal_link_expiry_job_callback(cast(ContextTypes.DEFAULT_TYPE, context))  # should not raise


async def test_mal_link_expiry_job_callback_is_a_noop_when_job_is_none(session_factory) -> None:
    context = MagicMock()
    context.job = None
    context.bot_data = {"session_factory": session_factory}

    await mal_link_expiry_job_callback(cast(ContextTypes.DEFAULT_TYPE, context))  # should not raise


def test_mal_link_expiry_job_name_is_stable_and_unique_per_player() -> None:
    assert mal_link_expiry_job_name(42) == mal_link_expiry_job_name(42)
    assert mal_link_expiry_job_name(42) != mal_link_expiry_job_name(43)
