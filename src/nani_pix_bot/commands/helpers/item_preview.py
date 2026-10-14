"""Sending a cosmetic's preview into a DM — shared by /cosmetics and /seasonshop.

Drawn on the viewer (#427); kept per previews.cache_key and language, then re-sent by
Telegram file_id (services/cosmetics/previews.py)."""

import asyncio
from collections.abc import Callable
from dataclasses import dataclass

from loguru import logger
from telegram import Message, User
from telegram.error import BadRequest, TelegramError
from telegram.ext import ContextTypes

from nani_pix_bot.db import session_scope
from nani_pix_bot.jobs import reveal_worker
from nani_pix_bot.jobs.avatars import fetch_avatar
from nani_pix_bot.jobs.reveal import AVATAR_TIMEOUT
from nani_pix_bot.models.enums import CosmeticSlot, PreviewKind
from nani_pix_bot.seasons.registry import ResolvedItem
from nani_pix_bot.services import i18n, players
from nani_pix_bot.services.cosmetics import previews, profile
from nani_pix_bot.services.reveal import pipeline


@dataclass(frozen=True)
class _Request:
    """One preview to send: of `resolved`, into `chat_id`, drawn on `viewer`, kept under
    `key` (previews.cache_key; None: never kept)."""

    chat_id: int
    viewer: User
    resolved: ResolvedItem
    lang: str
    key: str | None


async def _viewer(context: ContextTypes.DEFAULT_TYPE, request: _Request) -> previews.Viewer:
    """The viewer as a game shows them (the same name sources as a win badge and a flair),
    plus their avatar and own profile card only for the slots that draw them. The avatar is
    capped like the reveal's (AVATAR_TIMEOUT): updates are handled one at a time."""
    user, slot = request.viewer, request.resolved.item.slot
    card = None
    if slot is CosmeticSlot.PROFILE_BANNER:
        with session_scope(context.bot_data["session_factory"]) as session:
            card = profile.card(profile.build(session, user.id, request.lang), request.lang)
    avatar = None
    if slot in previews.AVATAR_SLOTS:
        try:
            avatar = await asyncio.wait_for(fetch_avatar(context.bot, user.id), AVATAR_TIMEOUT)
        except TimeoutError:
            logger.warning(
                "the avatar took over {seconds:g} s; previewing with initials",
                seconds=AVATAR_TIMEOUT,
            )
    handle = players.badge_handle(user.username, user.full_name)
    return previews.Viewer(user.id, handle, user.full_name, avatar, card)


async def _render(context: ContextTypes.DEFAULT_TYPE, request: _Request) -> previews.Preview:
    """The preview, rendered off the event loop: a clip in the reveal worker, the rest in a
    thread. No worker, or a render that fails, gives a short "can't show it now" text."""
    resolved, lang = request.resolved, request.lang
    try:
        viewer = await _viewer(context, request)
        job = previews.clip_job(resolved, viewer)
        if job is None:
            return await asyncio.to_thread(previews.render, resolved, lang, viewer)
        if reveal_worker.runtime(context.application) is not None:
            video = await reveal_worker.submit(context.bot_data, pipeline.preview_clip, *job.args())
            return previews.Preview(PreviewKind.VIDEO, video=video)
        logger.warning(
            "no reveal worker — can't render the preview of {item_id}", item_id=resolved.item_id
        )
    except Exception:
        logger.opt(exception=True).error(
            "rendering the preview of {item_id} failed", item_id=resolved.item_id
        )
    return previews.Preview(PreviewKind.TEXT, text=i18n.t("cosmetics.preview_unavailable", lang))


async def _send_media(
    context: ContextTypes.DEFAULT_TYPE,
    chat_id: int,
    kind: PreviewKind,
    media: bytes | str,
    caption: str | None = None,
) -> Message:
    if kind is PreviewKind.VIDEO:
        return await context.bot.send_video(chat_id=chat_id, video=media, supports_streaming=True)
    return await context.bot.send_photo(chat_id=chat_id, photo=media, caption=caption)


def _file_id(sent: Message, kind: PreviewKind) -> str | None:
    if kind is PreviewKind.VIDEO:
        return sent.video.file_id if sent.video else None
    return sent.photo[-1].file_id if sent.photo else None


async def _send_fresh(context: ContextTypes.DEFAULT_TYPE, request: _Request) -> None:
    """Renders, sends, then remembers the file_id (when it is kept) in a new short session."""
    preview = await _render(context, request)
    media = preview.video if preview.kind is PreviewKind.VIDEO else preview.image
    if media is None:
        await context.bot.send_message(chat_id=request.chat_id, text=preview.text or "")
        return
    sent = await _send_media(context, request.chat_id, preview.kind, media)
    file_id = _file_id(sent, preview.kind)
    if request.key is None:
        return
    if file_id is None:
        logger.warning(
            "Telegram returned no file_id for the preview of {item_id}",
            item_id=request.resolved.item_id,
        )
        return
    with session_scope(context.bot_data["session_factory"]) as session:
        previews.remember(session, request.key, request.lang, preview.kind, file_id)


async def _send_cached(
    context: ContextTypes.DEFAULT_TYPE, request: _Request, key: str, cached: tuple[PreviewKind, str]
) -> None:
    """Sends the remembered file_id; one Telegram refuses is forgotten and re-rendered."""
    try:
        await _send_media(context, request.chat_id, *cached)
    except BadRequest:
        logger.warning(
            "Telegram rejected the cached preview of {item_id} — re-rendering",
            item_id=request.resolved.item_id,
        )
        with session_scope(context.bot_data["session_factory"]) as session:
            previews.forget(session, key, request.lang)
        await _send_fresh(context, request)


def _cached(
    context: ContextTypes.DEFAULT_TYPE, request: _Request
) -> tuple[PreviewKind, str] | None:
    if request.key is None:
        return None
    with session_scope(context.bot_data["session_factory"]) as session:
        row = previews.cached(session, request.key, request.lang)
        return (PreviewKind(row.kind), row.file_id) if row is not None else None


async def send(
    context: ContextTypes.DEFAULT_TYPE,
    chat_id: int,
    resolved: ResolvedItem,
    lang: str,
    viewer: User,
) -> None:
    """`resolved`'s preview into `chat_id`, drawn on `viewer` (#427)."""
    key = previews.cache_key(resolved, viewer.id)
    request = _Request(chat_id, viewer, resolved, lang, key)
    cached = _cached(context, request)
    logger.info(
        "previewed {item_id} ({source})",
        item_id=resolved.item_id,
        source="cached" if cached else "rendered",
    )
    try:
        if key is None or cached is None:
            await _send_fresh(context, request)
        else:
            await _send_cached(context, request, key, cached)
    except TelegramError:
        logger.opt(exception=True).error(
            "failed to send the preview of {item_id}", item_id=resolved.item_id
        )


@dataclass(frozen=True)
class Picture:
    """A picture that is not a cosmetic (the clue token's icon): drawn by `render` once per
    `cache_key` and language, then re-sent by file_id."""

    cache_key: str
    render: Callable[[], bytes]
    caption: str | None = None


async def _send_picture_fresh(
    context: ContextTypes.DEFAULT_TYPE, chat_id: int, picture: Picture, lang: str
) -> None:
    try:
        image = await asyncio.to_thread(picture.render)
    except Exception:
        # Like _render for cosmetics: a broken drawing must not stop the
        # caller's next message (the offer page with its Buy button).
        logger.opt(exception=True).error(
            "drawing the image {cache_key} failed — sending a text note",
            cache_key=picture.cache_key,
        )
        text = i18n.t("cosmetics.preview_unavailable", lang)
        await context.bot.send_message(chat_id=chat_id, text=text)
        return
    sent = await _send_media(context, chat_id, PreviewKind.PHOTO, image, picture.caption)
    file_id = _file_id(sent, PreviewKind.PHOTO)
    if file_id is None:
        logger.warning(
            "Telegram returned no file_id for the image {cache_key}", cache_key=picture.cache_key
        )
        return
    with session_scope(context.bot_data["session_factory"]) as session:
        previews.remember(session, picture.cache_key, lang, PreviewKind.PHOTO, file_id)


async def _send_picture(
    context: ContextTypes.DEFAULT_TYPE, chat_id: int, picture: Picture, lang: str
) -> None:
    with session_scope(context.bot_data["session_factory"]) as session:
        row = previews.cached(session, picture.cache_key, lang)
        file_id = row.file_id if row is not None else None
    if file_id is not None:
        try:
            await _send_media(context, chat_id, PreviewKind.PHOTO, file_id, picture.caption)
            return
        except BadRequest:
            logger.warning(
                "Telegram rejected the cached image {cache_key} — drawing it again",
                cache_key=picture.cache_key,
            )
            with session_scope(context.bot_data["session_factory"]) as session:
                previews.forget(session, picture.cache_key, lang)
    await _send_picture_fresh(context, chat_id, picture, lang)


async def send_image(
    context: ContextTypes.DEFAULT_TYPE, chat_id: int, picture: Picture, lang: str
) -> None:
    logger.info("showed the image {cache_key}", cache_key=picture.cache_key)
    try:
        await _send_picture(context, chat_id, picture, lang)
    except TelegramError:
        logger.opt(exception=True).error(
            "failed to send the image {cache_key}", cache_key=picture.cache_key
        )
