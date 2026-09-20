"""Posting/pinning the group topic's "current image" (a pixelation
stage or a final reveal), and the shared "only clean up once the
announcement is confirmed sent" tail end every terminal (WON/UNSOLVED)
outcome needs — see MECHANICS.md's "Pixelation stages" and "Cleanup"
notes."""

from loguru import logger
from sqlalchemy.orm import Session, sessionmaker
from telegram import InputMediaPhoto, Message
from telegram.error import TelegramError
from telegram.ext import ContextTypes

from nani_pix_bot.db import session_scope
from nani_pix_bot.models.game import Game
from nani_pix_bot.services import game as game_service
from nani_pix_bot.services import settings


async def post_current_image(
    context: ContextTypes.DEFAULT_TYPE,
    session_factory: sessionmaker[Session],
    *,
    photo,
    caption: str,
) -> Message | None:
    """Sends `photo` to the game topic, then best-effort swaps the
    pinned "current image" for just this one message — see
    services/settings/bot_settings.py's get/set_pinned_message_id.
    Pin/unpin calls are wrapped and logged at WARNING on failure rather
    than raising: the bot may simply lack the "Pin messages" admin
    permission in the group, which shouldn't block the image itself
    from being posted. Takes chat_id/message_thread_id from
    `context.bot_data` rather than as parameters — every call site
    posts to the same group/topic, so there's never a different
    destination to pass in.

    Takes a `session_factory` rather than a live `session` deliberately:
    this function used to run inside whatever transaction the caller had
    open, so a Telegram timeout here (a real, recurring failure — see
    ARCHITECTURE.md's proxy-hop note) would roll back state the caller had
    already decided on, even though the message may have actually reached
    the group (a timeout means "no ack in time," not "didn't send"). It now
    owns its own short transaction for the pin bookkeeping, entirely
    decoupled from any caller's — callers are responsible for committing
    their own state before calling this. Returns `None` (rather than
    raising) if `send_photo` itself fails, so a flaky send never undoes
    work a caller already committed; callers that need to react to a
    failed announcement (e.g. gating cleanup on a confirmed send) check
    for `None`."""
    chat_id = context.bot_data["group_chat_id"]
    message_thread_id = context.bot_data["game_topic_id"]
    try:
        message = await context.bot.send_photo(
            chat_id=chat_id,
            message_thread_id=message_thread_id,
            photo=photo,
            caption=caption,
        )
    except TelegramError as exc:
        logger.warning("Failed to post the current image to chat {}: {}", chat_id, exc)
        return None

    with session_scope(session_factory) as session:
        previous_pinned_id = settings.get_pinned_message_id(session)
        if previous_pinned_id is not None:
            try:
                await context.bot.unpin_chat_message(chat_id=chat_id, message_id=previous_pinned_id)
            except TelegramError as exc:
                logger.warning(
                    "Failed to unpin message {} in chat {}: {}", previous_pinned_id, chat_id, exc
                )

        try:
            await context.bot.pin_chat_message(
                chat_id=chat_id, message_id=message.message_id, disable_notification=True
            )
            settings.set_pinned_message_id(session, message.message_id)
        except TelegramError as exc:
            logger.warning(
                "Failed to pin message {} in chat {}: {}", message.message_id, chat_id, exc
            )

    return message


async def post_current_images(
    context: ContextTypes.DEFAULT_TYPE,
    session_factory: sessionmaker[Session],
    *,
    photos: tuple[bytes, bytes],
    caption: str,
) -> tuple[Message, ...] | None:
    """Sends `photos` to the game topic as a single 2-photo album (a
    Telegram media group), then best-effort swaps the pinned "current
    image" for the album's first message — see post_current_image's
    docstring for the full pin/session-factory contract this mirrors
    (same session-factory-not-live-session reasoning, same "pin/unpin
    failures are logged and swallowed, never raised" behaviour, same
    "returns None rather than raising on a failed send" contract).

    Telegram pins a single message, not an album as a construct — a
    viewer's pinned-message preview will show only the first photo, not
    both. Same best-effort spirit as post_current_image's own
    pin-permission-failure handling: this is a known, accepted
    limitation, not a bug to work around."""
    chat_id = context.bot_data["group_chat_id"]
    message_thread_id = context.bot_data["game_topic_id"]
    try:
        result = await context.bot.send_media_group(
            chat_id=chat_id,
            message_thread_id=message_thread_id,
            media=[
                InputMediaPhoto(media=photos[0], caption=caption),
                InputMediaPhoto(media=photos[1]),
            ],
        )
    except TelegramError as exc:
        logger.warning("Failed to post the current images to chat {}: {}", chat_id, exc)
        return None

    first_message_id = result[0].message_id
    with session_scope(session_factory) as session:
        previous_pinned_id = settings.get_pinned_message_id(session)
        if previous_pinned_id is not None:
            try:
                await context.bot.unpin_chat_message(chat_id=chat_id, message_id=previous_pinned_id)
            except TelegramError as exc:
                logger.warning(
                    "Failed to unpin message {} in chat {}: {}", previous_pinned_id, chat_id, exc
                )

        try:
            await context.bot.pin_chat_message(
                chat_id=chat_id, message_id=first_message_id, disable_notification=True
            )
            settings.set_pinned_message_id(session, first_message_id)
        except TelegramError as exc:
            logger.warning(
                "Failed to pin message {} in chat {}: {}", first_message_id, chat_id, exc
            )

    return tuple(result)


def clear_image_if_sent(
    session_factory: sessionmaker[Session], game_id: int, sent: Message | tuple[Message, ...] | None
) -> None:
    """Shared tail end of a terminal (WON/UNSOLVED) reveal: only drop the
    stored screenshot bytes once the reveal is confirmed sent (see
    MECHANICS.md's "Cleanup" note) — re-fetches the row in its own
    session since the caller's committing session has already closed by
    the time `sent` is known. A no-op if `sent` is None (the announcement
    failed) or the row is somehow already gone.

    `sent` accepts either a single `Message` (post_current_image's normal
    ruleset reveal) or a `tuple[Message, ...]` (post_current_images'
    hard-mode 2-photo album reveal) — only ever checked for `is None`
    here, never inspected for its internal shape, so both callers share
    this one function untouched."""
    if sent is None:
        return
    with session_scope(session_factory) as session:
        game = session.get(Game, game_id)
        if game is not None:
            if game.hard_mode:
                game_service.clear_hard_mode_images(game)
            else:
                game_service.clear_original_screenshot(game)
