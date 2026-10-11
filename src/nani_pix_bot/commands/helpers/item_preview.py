"""Sending a cosmetic's preview into a DM — shared by /cosmetics and /seasonshop.

Rendered once per (item, language), then re-sent by Telegram file_id
(services/cosmetics/previews.py)."""

import asyncio
from collections.abc import Callable
from dataclasses import dataclass

from loguru import logger
from telegram import Message
from telegram.error import BadRequest, TelegramError
from telegram.ext import ContextTypes

from nani_pix_bot.db import session_scope
from nani_pix_bot.jobs import reveal_worker
from nani_pix_bot.models.enums import PreviewKind
from nani_pix_bot.seasons.registry import ResolvedItem
from nani_pix_bot.services import i18n
from nani_pix_bot.services.cosmetics import previews
from nani_pix_bot.services.reveal import pipeline


async def _render(
    context: ContextTypes.DEFAULT_TYPE, resolved: ResolvedItem, lang: str
) -> previews.Preview:
    """The preview, rendered off the event loop: a clip in the reveal worker, the rest in a
    thread. No worker, or a render that fails, gives a short "can't show it now" text."""
    try:
        job = previews.clip_job(resolved, lang)
        if job is None:
            return await asyncio.to_thread(previews.render, resolved, lang)
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


async def _send_fresh(
    context: ContextTypes.DEFAULT_TYPE, chat_id: int, resolved: ResolvedItem, lang: str
) -> None:
    """Renders, sends, then remembers the file_id in a new short session."""
    preview = await _render(context, resolved, lang)
    media = preview.video if preview.kind is PreviewKind.VIDEO else preview.image
    if media is None:
        await context.bot.send_message(chat_id=chat_id, text=preview.text or "")
        return
    sent = await _send_media(context, chat_id, preview.kind, media)
    file_id = _file_id(sent, preview.kind)
    if file_id is None:
        logger.warning(
            "Telegram returned no file_id for the preview of {item_id}", item_id=resolved.item_id
        )
        return
    with session_scope(context.bot_data["session_factory"]) as session:
        previews.remember(session, resolved.item_id, lang, preview.kind, file_id)


async def _send_cached(
    context: ContextTypes.DEFAULT_TYPE,
    chat_id: int,
    resolved: ResolvedItem,
    lang: str,
    cached: tuple[PreviewKind, str],
) -> None:
    """Sends the remembered file_id; one Telegram refuses is forgotten and re-rendered."""
    try:
        await _send_media(context, chat_id, *cached)
    except BadRequest:
        logger.warning(
            "Telegram rejected the cached preview of {item_id} — re-rendering",
            item_id=resolved.item_id,
        )
        with session_scope(context.bot_data["session_factory"]) as session:
            previews.forget(session, resolved.item_id, lang)
        await _send_fresh(context, chat_id, resolved, lang)


async def _send_preview(
    context: ContextTypes.DEFAULT_TYPE,
    chat_id: int,
    resolved: ResolvedItem,
    lang: str,
    cached: tuple[PreviewKind, str] | None,
) -> None:
    try:
        if cached is None:
            await _send_fresh(context, chat_id, resolved, lang)
        else:
            await _send_cached(context, chat_id, resolved, lang, cached)
    except TelegramError:
        logger.opt(exception=True).error(
            "failed to send the preview of {item_id}", item_id=resolved.item_id
        )


async def send(
    context: ContextTypes.DEFAULT_TYPE, chat_id: int, resolved: ResolvedItem, lang: str
) -> None:
    with session_scope(context.bot_data["session_factory"]) as session:
        row = previews.cached(session, resolved.item_id, lang)
        cached = (PreviewKind(row.kind), row.file_id) if row is not None else None
    logger.info(
        "previewed {item_id} ({source})",
        item_id=resolved.item_id,
        source="cached" if cached else "rendered",
    )
    await _send_preview(context, chat_id, resolved, lang, cached)


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
    image = await asyncio.to_thread(picture.render)
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
