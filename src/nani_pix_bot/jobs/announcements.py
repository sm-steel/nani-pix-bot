"""Posts announcement_outbox rows into the game topic (spec §3, §6, §7): one
repeating job, id order, nothing during quiet hours (rows simply wait), a
row that keeps failing is given up after outbox.MAX_ATTEMPTS. Each post is
an image card with its text as caption; three or more unlocks from the
same event go out as one album; a card that can't be drawn goes out as
text. Durable rows are why this needs no restart re-arming beyond starting
the job in app.py's _post_init (plan clarification 3). Delivery is
at-least-once: a crash between the send and mark_posted re-posts that row."""

import asyncio
import re
from collections.abc import Callable
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from typing import TypedDict

from loguru import logger
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker
from telegram import InputMediaPhoto
from telegram.error import TelegramError
from telegram.ext import ContextTypes, JobQueue

from nani_pix_bot.db import session_scope
from nani_pix_bot.jobs.avatars import fetch_avatar
from nani_pix_bot.jobs.timers._shared import job_log_scope
from nani_pix_bot.models.achievement import AchievementGrant
from nani_pix_bot.models.announcement import AnnouncementOutbox
from nani_pix_bot.models.enums import OutboxKind, Rarity
from nani_pix_bot.models.period import PeriodResult
from nani_pix_bot.services import i18n, players, quiet_hours, settings
from nani_pix_bot.services.achievements import catalogue, names, outbox
from nani_pix_bot.services.cards import (
    PodiumCard,
    PodiumEntry,
    UnlockCard,
    load_background,
    podium_slot,
    render_podium_card,
    render_unlock_card,
    unlock_slot,
)

OUTBOX_JOB_NAME = "announcement_outbox"
OUTBOX_POLL_SECONDS = 20
OUTBOX_BATCH = 20
ALBUM_MIN = 3
ALBUM_MAX = 10  # Telegram's album size limit
_MEDALS = ("🥇", "🥈", "🥉")


class _Where(TypedDict):
    chat_id: int
    message_thread_id: int


@dataclass(frozen=True)
class Post:
    row_ids: tuple[int, ...]
    text: str
    card: UnlockCard | PodiumCard | None = None
    avatar_ids: tuple[int, ...] = ()
    batch_id: int | None = None
    background_slot: str | None = None


def _card_text(text: str) -> str:
    """Noto Sans has no 💠 glyph (the card draws its own diamond): drop it
    together with the space before it."""
    return re.sub(r"\s+", " ", re.sub(r"\s*💠", "", text)).strip()


def _unlock_post(session: Session, row: AnnouncementOutbox, lang: str) -> Post | None:
    granted = session.get(AchievementGrant, row.payload["grant_id"])
    if granted is None:
        return None
    defn = catalogue.get(granted.key)
    who = players.display_name(session, granted.player_id)
    name = names.title(defn, granted.tier, granted.period_key, lang)
    desc = names.description(defn, granted.tier, lang)
    rarity = i18n.t(f"achievement.rarity.{granted.rarity}", lang)
    text = i18n.t(
        "achievement.unlocked",
        lang,
        player=who,
        name=name,
        desc=desc,
        rarity=rarity,
        reward=granted.reward,
        points=granted.points,
    )
    card = UnlockCard(
        headline=i18n.t("card.unlocked", lang),
        name=name,
        description=_card_text(desc),
        handle=who,
        reward=granted.reward,
        points_label=i18n.t("card.points", lang, points=granted.points),
        rarity_label=rarity,
        rarity=Rarity(granted.rarity),
        seed=granted.player_id,
    )
    return Post(
        (row.id,),
        text,
        card,
        (granted.player_id,),
        row.batch_id,
        unlock_slot(card.rarity),
    )


def _results(session: Session, ptype: str, key: str) -> list[PeriodResult]:
    stmt = (
        select(PeriodResult)
        .where(PeriodResult.period_type == ptype, PeriodResult.period_key == key)
        .order_by(PeriodResult.rank)
    )
    return list(session.scalars(stmt))


def _podium_lines(session: Session, results: list[PeriodResult], lang: str) -> list[str]:
    return [
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


def _podium_card(
    session: Session, results: list[PeriodResult], title: str, lang: str
) -> PodiumCard:
    entries = tuple(
        PodiumEntry(
            players.display_name(session, r.player_id),
            i18n.t("card.score", lang, score=r.score, wins=r.wins),
            r.player_id,
        )
        for r in results
    )
    return PodiumCard(title, entries)


def _period_post(session: Session, row: AnnouncementOutbox, lang: str) -> Post | None:
    ptype, key = row.payload["period_type"], row.payload["period_key"]
    results = _results(session, ptype, key)
    if not results:
        return None
    label = names.period_label(key, lang)
    header = i18n.t(f"period.summary.{ptype}", lang, period=label)
    text = "\n".join([header, *_podium_lines(session, results, lang)])
    card = _podium_card(session, results, i18n.t(f"card.podium.{ptype}", lang, period=label), lang)
    return Post(
        (row.id,),
        text,
        card,
        tuple(r.player_id for r in results),
        background_slot=podium_slot(ptype),
    )


RENDERERS: dict[OutboxKind, Callable[[Session, AnnouncementOutbox, str], Post | None]] = {
    OutboxKind.UNLOCK: _unlock_post,
    OutboxKind.PERIOD_SUMMARY: _period_post,
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
        post = RENDERERS[OutboxKind(row.kind)](session, row, lang)
    except (KeyError, ValueError) as exc:
        logger.error(
            "announcement {outbox_id} could not be rendered: {error!r}", outbox_id=row.id, error=exc
        )
        outbox.mark_failed(session, [row.id])
        return None
    return post or Post((row.id,), "")


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


def _batches(posts: list[Post]) -> list[list[Post]]:
    """Consecutive posts from the same event, as one album when there are
    ALBUM_MIN or more of them; everything else one by one, in id order."""
    groups: list[list[Post]] = []
    for post in posts:
        same = groups and post.batch_id is not None and groups[-1][-1].batch_id == post.batch_id
        if same:
            groups[-1].append(post)
        else:
            groups.append([post])
    batches: list[list[Post]] = []
    for group in groups:
        for start in range(0, len(group), ALBUM_MAX):
            chunk = group[start : start + ALBUM_MAX]
            if len(chunk) >= ALBUM_MIN:
                batches.append(chunk)
            else:
                batches.extend([post] for post in chunk)
    return batches


def _draw(post: Post, faces: list[bytes | None]) -> bytes:
    """Render the post's card on a worker thread: background lookup included."""
    slot = post.background_slot
    seed = post.avatar_ids[0] if post.avatar_ids else 0
    background = load_background(slot, seed) if slot else None
    card = post.card
    if isinstance(card, UnlockCard):
        return render_unlock_card(card, faces[0] if faces else None, background)
    if isinstance(card, PodiumCard):
        entries = tuple(replace(e, avatar=a) for e, a in zip(card.entries, faces, strict=False))
        return render_podium_card(replace(card, entries=entries), background)
    raise ValueError("post has no card")


async def _image(context: ContextTypes.DEFAULT_TYPE, post: Post) -> bytes | None:
    if post.card is None:
        return None
    faces = [await fetch_avatar(context.bot, uid) for uid in post.avatar_ids]
    try:
        return await asyncio.to_thread(_draw, post, faces)
    except (OSError, ValueError) as exc:
        logger.error(
            "couldn't draw the card for {outbox_ids}: {error} — posting text",
            outbox_ids=post.row_ids,
            error=exc,
        )
        return None


def _where(context: ContextTypes.DEFAULT_TYPE) -> _Where:
    return {
        "chat_id": context.bot_data["group_chat_id"],
        "message_thread_id": context.bot_data["game_topic_id"],
    }


async def _send_one(context: ContextTypes.DEFAULT_TYPE, post: Post) -> None:
    if not post.text:
        return
    image = await _image(context, post)
    if image is None:
        await context.bot.send_message(text=post.text, **_where(context))
    else:
        await context.bot.send_photo(photo=image, caption=post.text, **_where(context))


async def _send_album(context: ContextTypes.DEFAULT_TYPE, group: list[Post]) -> None:
    images = [await _image(context, post) for post in group]
    if any(image is None for image in images):
        for post in group:
            await _send_one(context, post)
        return
    media = [
        InputMediaPhoto(image, caption=post.text)
        for image, post in zip(images, group, strict=True)
        if image is not None
    ]
    await context.bot.send_media_group(media=media, **_where(context))


async def _send(context: ContextTypes.DEFAULT_TYPE, group: list[Post]) -> bool:
    ids = tuple(i for post in group for i in post.row_ids)
    if all(not post.text for post in group):
        logger.warning("announcement {outbox_ids} had nothing left to say", outbox_ids=ids)
        return True
    try:
        await (_send_album(context, group) if len(group) > 1 else _send_one(context, group[0]))
    except TelegramError as exc:
        logger.error("failed to post announcement {outbox_ids}: {error}", outbox_ids=ids, error=exc)
        return False
    logger.info("posted announcement {outbox_ids}", outbox_ids=ids)
    return True


@job_log_scope()
async def drain_outbox(context: ContextTypes.DEFAULT_TYPE) -> None:
    session_factory = context.bot_data["session_factory"]
    for group in _batches(_collect(session_factory)):
        sent = await _send(context, group)
        ids = [i for post in group for i in post.row_ids]
        with session_scope(session_factory) as session:
            if sent:
                outbox.mark_posted(session, ids)
            else:
                outbox.mark_failed(session, ids)
        if not sent:
            # Stop here: the rest stay pending, untouched and in order, so a
            # short outage neither burns their attempts nor reorders them.
            return
