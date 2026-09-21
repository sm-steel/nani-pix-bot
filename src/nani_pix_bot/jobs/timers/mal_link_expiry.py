"""Expires an abandoned /linkmal attempt — mirrors
jobs/timers/setup_abandon.py's exact shape (a scheduled JobQueue job).

Unlike setup_abandon.py, this timer is **not** the only thing enforcing
its own deadline: this bot's job callbacks never survive a restart, so a
restart mid-link would leave the pending_mal_link row behind forever —
and unlike an abandoned setup row, a stale pending link actively
hijacks every later plain-text DM from that player (see
services/mal_link.py's `get_pending_link`, which enforces the same
MAL_LINK_EXPIRY_DELAY on read and deletes what it finds expired). This
job is the tidy-up that keeps the table from carrying rows nobody ever
reads again; correctness no longer depends on it firing."""

from typing import cast

from loguru import logger
from telegram.ext import ContextTypes, JobQueue

from nani_pix_bot.db import session_scope
from nani_pix_bot.services import mal_link

# Re-exported from services/mal_link.py (where it lives so the read-side
# TTL check can use it too, without services/ importing jobs/) — the
# schedule below and that check must never drift apart.
MAL_LINK_EXPIRY_DELAY = mal_link.MAL_LINK_EXPIRY_DELAY


def mal_link_expiry_job_name(telegram_user_id: int) -> str:
    """Deterministic JobQueue job name for a player's pending-link
    expiry timer — mirrors setup_abandon.setup_abandon_job_name()."""
    return f"mal-link-expiry-{telegram_user_id}"


def schedule_mal_link_expiry(job_queue: JobQueue | None, telegram_user_id: int) -> None:
    if job_queue is None:
        return
    logger.debug(
        "Scheduling MAL link expiry for player {} in {}", telegram_user_id, MAL_LINK_EXPIRY_DELAY
    )
    job_queue.run_once(
        mal_link_expiry_job_callback,
        when=MAL_LINK_EXPIRY_DELAY,
        name=mal_link_expiry_job_name(telegram_user_id),
        data=telegram_user_id,
    )


async def mal_link_expiry_job_callback(context: ContextTypes.DEFAULT_TYPE) -> None:
    """Fires 10min after a /linkmal attempt starts. If the player never
    came back with a code, the pending_mal_link row is still there —
    delete it. If they already completed (or restarted) the flow, or
    `get_pending_link`'s own TTL check already swept the row on a read
    that happened first, it's gone and this is a no-op, not an error."""
    job = context.job
    if job is None:
        return
    telegram_user_id = cast(int, job.data)

    session_factory = context.bot_data["session_factory"]
    with session_scope(session_factory) as session:
        if mal_link.get_pending_link(session, telegram_user_id) is None:
            logger.debug(
                "MAL link expiry fired for player {} but it's already resolved",
                telegram_user_id,
            )
            return
        logger.info("Player {}'s /linkmal attempt expired after 10min unused", telegram_user_id)
        mal_link.delete_pending_link(session, telegram_user_id)
