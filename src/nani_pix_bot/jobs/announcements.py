"""Posts announcement_outbox rows into the game topic (spec §3, §6): one
repeating job, id order, nothing during quiet hours (rows simply wait), a
row that keeps failing is given up after outbox.MAX_ATTEMPTS. Durable rows
are why this needs no restart re-arming beyond starting the job in
app.py's _post_init (plan clarification 3). Delivery is at-least-once: a
crash between the send and mark_posted re-posts that row."""

from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime

from loguru import logger
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker
from telegram.error import TelegramError
from telegram.ext import ContextTypes, JobQueue

from nani_pix_bot.db import session_scope
from nani_pix_bot.jobs.timers._shared import job_log_scope
from nani_pix_bot.models.achievement import AchievementGrant
from nani_pix_bot.models.announcement import AnnouncementOutbox
from nani_pix_bot.models.enums import OutboxKind
from nani_pix_bot.models.period import PeriodResult
from nani_pix_bot.services import i18n, players, quiet_hours, settings
from nani_pix_bot.services.achievements import catalogue, names, outbox

OUTBOX_JOB_NAME = "announcement_outbox"
OUTBOX_POLL_SECONDS = 20
OUTBOX_BATCH = 20


@dataclass(frozen=True)
class Post:
    row_ids: tuple[int, ...]
    text: str


def _unlock_text(session: Session, row: AnnouncementOutbox, lang: str) -> str | None:
    granted = session.get(AchievementGrant, row.payload["grant_id"])
    if granted is None:
        return None
    defn = catalogue.get(granted.key)
    return i18n.t(
        "achievement.unlocked",
        lang,
        player=players.display_name(session, granted.player_id),
        name=names.title(defn, granted.tier, granted.period_key, lang),
        desc=names.description(defn, granted.tier, lang),
        rarity=i18n.t(f"achievement.rarity.{granted.rarity}", lang),
        reward=granted.reward,
        points=granted.points,
    )


_MEDALS = ("🥇", "🥈", "🥉")


def _period_text(session: Session, row: AnnouncementOutbox, lang: str) -> str | None:
    ptype, key = row.payload["period_type"], row.payload["period_key"]
    stmt = (
        select(PeriodResult)
        .where(PeriodResult.period_type == ptype, PeriodResult.period_key == key)
        .order_by(PeriodResult.rank)
    )
    results = list(session.scalars(stmt))
    if not results:
        return None
    header = i18n.t(f"period.summary.{ptype}", lang, period=names.period_label(key, lang))
    lines = [
        i18n.t(
            "period.summary.row",
            lang,
            medal=_MEDALS[r.rank - 1],
            player=players.display_name(session, r.player_id),
            score=r.score,
            wins=r.wins,
        )
        for r in results
    ]
    return "\n".join([header, *lines])


RENDERERS: dict[OutboxKind, Callable[[Session, AnnouncementOutbox, str], str | None]] = {
    OutboxKind.UNLOCK: _unlock_text,
    OutboxKind.PERIOD_SUMMARY: _period_text,
}


def schedule_outbox_drain(job_queue: JobQueue | None) -> None:
    if job_queue is None:
        return
    job_queue.run_repeating(
        drain_outbox, interval=OUTBOX_POLL_SECONDS, first=OUTBOX_POLL_SECONDS, name=OUTBOX_JOB_NAME
    )


def _render(session: Session, row: AnnouncementOutbox, lang: str) -> Post | None:
    """One row's post, or None when it can't be rendered. Such a row is
    marked failed on its own, so it can't block the rows behind it."""
    try:
        text = RENDERERS[OutboxKind(row.kind)](session, row, lang)
    except (KeyError, ValueError) as exc:
        logger.error(
            "announcement {outbox_id} could not be rendered: {error!r}", outbox_id=row.id, error=exc
        )
        outbox.mark_failed(session, [row.id])
        return None
    return Post((row.id,), text or "")


def _collect(session_factory: sessionmaker[Session]) -> list[Post]:
    with session_scope(session_factory) as session:
        if quiet_hours.is_quiet(settings.get_quiet_hours(session), datetime.now(UTC)):
            logger.debug("quiet hours — announcements wait")
            return []
        lang = settings.get_language(session)
        posts = []
        for row in outbox.pending(session, OUTBOX_BATCH):
            post = _render(session, row, lang)
            if post is not None:
                posts.append(post)
        return posts


async def _send(context: ContextTypes.DEFAULT_TYPE, post: Post) -> bool:
    if not post.text:
        logger.warning("announcement {outbox_ids} had nothing left to say", outbox_ids=post.row_ids)
        return True
    try:
        await context.bot.send_message(
            chat_id=context.bot_data["group_chat_id"],
            message_thread_id=context.bot_data["game_topic_id"],
            text=post.text,
        )
    except TelegramError as exc:
        logger.error(
            "failed to post announcement {outbox_ids}: {error}", outbox_ids=post.row_ids, error=exc
        )
        return False
    logger.info("posted announcement {outbox_ids}", outbox_ids=post.row_ids)
    return True


@job_log_scope()
async def drain_outbox(context: ContextTypes.DEFAULT_TYPE) -> None:
    session_factory = context.bot_data["session_factory"]
    for post in _collect(session_factory):
        sent = await _send(context, post)
        with session_scope(session_factory) as session:
            if sent:
                outbox.mark_posted(session, post.row_ids)
            else:
                outbox.mark_failed(session, post.row_ids)
        if not sent:
            # Stop here: the rest stay pending, untouched and in order, so a
            # short outage neither burns their attempts nor reorders them.
            return
