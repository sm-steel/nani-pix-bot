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
from itertools import groupby
from typing import TypedDict
from zoneinfo import ZoneInfo

from loguru import logger
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
from nani_pix_bot.models.season import SeasonSchedule
from nani_pix_bot.seasons import registry
from nani_pix_bot.seasons.definition import SeasonRun
from nani_pix_bot.services import i18n, players, quiet_hours, settings
from nani_pix_bot.services.achievements import catalogue, names, outbox, podium
from nani_pix_bot.services.cards import (
    PodiumCard,
    SeasonBanner,
    UnlockCard,
    load_background,
    podium_slot,
    render_podium_card,
    render_season_banner,
    render_unlock_card,
    season_background,
    unlock_slot,
)
from nani_pix_bot.services.events import as_utc
from nani_pix_bot.services.seasons import lifecycle

OUTBOX_JOB_NAME = "announcement_outbox"
OUTBOX_POLL_SECONDS = 20
OUTBOX_BATCH = 20
ALBUM_MIN = 3
ALBUM_MAX = 10  # Telegram's album size limit


class _Where(TypedDict):
    chat_id: int
    message_thread_id: int


@dataclass(frozen=True)
class Post:
    row_ids: tuple[int, ...]
    text: str
    card: UnlockCard | PodiumCard | SeasonBanner | None = None
    avatar_ids: tuple[int, ...] = ()
    batch_id: int | None = None
    background_slot: str | None = None
    background: bytes | None = None


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


def _period_post(session: Session, row: AnnouncementOutbox, lang: str) -> Post | None:
    ptype, key = row.payload["period_type"], row.payload["period_key"]
    results = podium.results(session, ptype, key)
    if not results:
        return None
    label = names.period_label(key, lang)
    header = i18n.t(f"period.summary.{ptype}", lang, period=label)
    text = "\n".join([header, *podium.lines(session, results, lang)])
    card = podium.card(session, results, i18n.t(f"card.podium.{ptype}", lang, period=label), lang)
    return Post(
        (row.id,),
        text,
        card,
        tuple(r.player_id for r in results),
        background_slot=podium_slot(ptype),
    )


def _local(at: datetime, tz: ZoneInfo) -> str:
    return as_utc(at).astimezone(tz).strftime("%d.%m %H:%M")


def _season_lines(
    session: Session, kind: OutboxKind, season: SeasonSchedule, run: SeasonRun, lang: str
) -> list[str]:
    tz = settings.get_group_timezone(session)
    when = {"start": _local(season.start_at, tz), "end": _local(season.end_at, tz), "tz": tz.key}
    lines = [i18n.t(f"season.post.{kind.value}", lang, name=run.name(lang), **when)]
    if kind is OutboxKind.SEASON_START and run.gate is not None:
        rule = run.gate.description.get(lang) or run.gate.description["EN"]
        lines.append(i18n.t("season.post.gate", lang, rule=rule))
    if kind is OutboxKind.SEASON_END:
        lines.extend(
            i18n.t(
                "season.post.podium_line",
                lang,
                rank=placed.rank,
                player=players.display_name(session, placed.player_id),
                xp=placed.xp,
            )
            for placed in lifecycle.podium(session, season.id)[:3]
        )
    return lines


def _season_post(
    kind: OutboxKind,
) -> Callable[[Session, AnnouncementOutbox, str], Post | None]:
    def render(session: Session, row: AnnouncementOutbox, lang: str) -> Post | None:
        season = session.get(SeasonSchedule, row.payload["season_id"])
        run = registry.get(season.run_id) if season is not None else None
        if season is None or run is None:
            return None
        tz = settings.get_group_timezone(session)
        banner = SeasonBanner(
            headline=i18n.t(f"card.season.{kind.value}", lang),
            title=run.name(lang),
            subtitle=f"{_local(season.start_at, tz)} \u2013 {_local(season.end_at, tz)} ({tz.key})",
        )
        return Post(
            (row.id,),
            "\n".join(_season_lines(session, kind, season, run, lang)),
            banner,
            background=season_background(season.run_id),
        )

    return render


RENDERERS: dict[OutboxKind, Callable[[Session, AnnouncementOutbox, str], Post | None]] = {
    OutboxKind.UNLOCK: _unlock_post,
    OutboxKind.PERIOD_SUMMARY: _period_post,
    OutboxKind.SEASON_TEASER: _season_post(OutboxKind.SEASON_TEASER),
    OutboxKind.SEASON_START: _season_post(OutboxKind.SEASON_START),
    OutboxKind.SEASON_END: _season_post(OutboxKind.SEASON_END),
}


def schedule_outbox_drain(job_queue: JobQueue | None) -> None:
    if job_queue is None:
        return
    job_queue.run_repeating(
        drain_outbox, interval=OUTBOX_POLL_SECONDS, first=OUTBOX_POLL_SECONDS, name=OUTBOX_JOB_NAME
    )


def _render(session: Session, row: AnnouncementOutbox, lang: str) -> Post | None:
    """One row's post, or None when it can't be rendered. Such a row is
    marked failed on its own, so it can't block the rows behind it —
    whatever the renderer raised."""
    try:
        post = RENDERERS[OutboxKind(row.kind)](session, row, lang)
    except Exception as exc:
        logger.opt(exception=True).error(
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


def _event_key(post: Post) -> object:
    """Posts of one event share a key; one without an event is its own."""
    return post.batch_id if post.batch_id is not None else ("row", post.row_ids)


def _albums(group: list[Post]) -> list[list[Post]]:
    """One event's posts in chunks of at most ALBUM_MAX; a chunk under
    ALBUM_MIN is not an album, so its posts go out one by one."""
    chunks = [group[i : i + ALBUM_MAX] for i in range(0, len(group), ALBUM_MAX)]
    return [
        piece
        for chunk in chunks
        for piece in ([chunk] if len(chunk) >= ALBUM_MIN else [[post] for post in chunk])
    ]


def _batches(posts: list[Post]) -> list[list[Post]]:
    """Consecutive posts from the same event, as one album when there are
    ALBUM_MIN or more of them; everything else one by one, in id order."""
    return [piece for _, group in groupby(posts, _event_key) for piece in _albums(list(group))]


def _draw(post: Post, faces: list[bytes | None]) -> bytes:
    """Render the post's card on a worker thread: background lookup included."""
    slot = post.background_slot
    seed = post.avatar_ids[0] if post.avatar_ids else 0
    background = post.background
    if background is None and slot:
        background = load_background(slot, seed)
    card = post.card
    if isinstance(card, UnlockCard):
        return render_unlock_card(card, faces[0] if faces else None, background)
    if isinstance(card, SeasonBanner):
        return render_season_banner(card, background)
    if isinstance(card, PodiumCard):
        entries = tuple(replace(e, avatar=a) for e, a in zip(card.entries, faces, strict=False))
        return render_podium_card(replace(card, entries=entries), background)
    raise ValueError("post has no card")


AvatarCache = dict[int, bytes | None]


async def _faces(
    context: ContextTypes.DEFAULT_TYPE, post: Post, cache: AvatarCache
) -> list[bytes | None]:
    """The post's avatars; each player is fetched once per drain, not once per card."""
    for user_id in post.avatar_ids:
        if user_id not in cache:
            cache[user_id] = await fetch_avatar(context.bot, user_id)
    return [cache[user_id] for user_id in post.avatar_ids]


async def _image(
    context: ContextTypes.DEFAULT_TYPE, post: Post, cache: AvatarCache
) -> bytes | None:
    if post.card is None:
        return None
    faces = await _faces(context, post, cache)
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


async def _send_one(context: ContextTypes.DEFAULT_TYPE, post: Post, image: bytes | None) -> None:
    """A photo with the caption, or the text alone when there's no card."""
    if not post.text:
        return
    if image is None:
        await context.bot.send_message(text=post.text, **_where(context))
    else:
        await context.bot.send_photo(photo=image, caption=post.text, **_where(context))


def _row_ids(group: list[Post]) -> list[int]:
    return [i for post in group for i in post.row_ids]


async def _send_album(
    context: ContextTypes.DEFAULT_TYPE, group: list[Post], images: list[bytes]
) -> None:
    media = [
        InputMediaPhoto(image, caption=post.text) for image, post in zip(images, group, strict=True)
    ]
    await context.bot.send_media_group(media=media, **_where(context))


async def _send_each(
    context: ContextTypes.DEFAULT_TYPE, group: list[Post], images: list[bytes | None]
) -> tuple[list[int], list[int]]:
    """One message per post, reusing the bytes already rendered (a card that
    failed to draw goes out as text). A failure stops here: the posts before
    it stay posted, it and the rest are reported failed."""
    for done, (post, image) in enumerate(zip(group, images, strict=True)):
        try:
            await _send_one(context, post, image)
        except TelegramError as exc:
            logger.error(
                "failed to post announcement {outbox_ids}: {error}",
                outbox_ids=post.row_ids,
                error=exc,
            )
            return _row_ids(group[:done]), _row_ids(group[done:])
    return _row_ids(group), []


async def _send(
    context: ContextTypes.DEFAULT_TYPE, group: list[Post], cache: AvatarCache
) -> tuple[list[int], list[int]]:
    """Post a group; returns (posted row ids, failed row ids). A group of
    ALBUM_MIN or more goes out as one album if every card drew, otherwise
    post by post from the images already rendered; nothing is drawn twice."""
    ids = _row_ids(group)
    if all(not post.text for post in group):
        logger.warning("announcement {outbox_ids} had nothing left to say", outbox_ids=ids)
        return ids, []
    images = [await _image(context, post, cache) for post in group]
    rendered = [image for image in images if image is not None]
    if len(group) > 1 and len(rendered) == len(group):
        try:
            await _send_album(context, group, rendered)
        except TelegramError as exc:
            logger.error(
                "failed to post announcement {outbox_ids}: {error}", outbox_ids=ids, error=exc
            )
            return [], ids
        posted, failed = ids, []
    else:
        posted, failed = await _send_each(context, group, images)
    if posted:
        logger.info("posted announcement {outbox_ids}", outbox_ids=posted)
    return posted, failed


@job_log_scope()
async def drain_outbox(context: ContextTypes.DEFAULT_TYPE) -> None:
    session_factory = context.bot_data["session_factory"]
    cache: AvatarCache = {}
    for group in _batches(_collect(session_factory)):
        posted, failed = await _send(context, group, cache)
        with session_scope(session_factory) as session:
            if posted:
                outbox.mark_posted(session, posted)
            if failed:
                outbox.mark_failed(session, failed)
        if failed:
            # Stop here: the rest stay pending, untouched and in order, so a
            # short outage neither burns their attempts nor reorders them.
            return
