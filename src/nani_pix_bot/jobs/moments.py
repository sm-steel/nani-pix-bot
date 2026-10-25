"""Posting a moment card (Halloween) to the game topic: a charm used in a round,
as a picture with the plain line as its caption. A card that can't be drawn or
sent never blocks the game: the caption goes out as text instead."""

import asyncio
from dataclasses import dataclass

from loguru import logger
from telegram.error import TelegramError
from telegram.ext import ContextTypes

from nani_pix_bot.jobs.avatars import fetch_avatar
from nani_pix_bot.models.enums import ConsumableKind
from nani_pix_bot.services.cards import moments as moment_cards
from nani_pix_bot.services.cards.moments import Face, Mood
from nani_pix_bot.services.cosmetics import consumable_card


@dataclass(frozen=True)
class CharmMoment:
    kind: ConsumableKind  # whose art the card shows
    user_id: int
    display_name: str  # the name pill: plain, no flair
    title: str
    caption: str  # the line the plain text would have been


def _render(moment: CharmMoment, avatar: bytes | None) -> bytes:
    face = Face(avatar, moment.display_name, moment.user_id)
    return moment_cards.charm_card(Mood.CHARM, face, consumable_card.art(moment.kind), moment.title)


async def _send_text(context: ContextTypes.DEFAULT_TYPE, moment: CharmMoment, game_id: int) -> None:
    try:
        await context.bot.send_message(
            chat_id=context.bot_data["group_chat_id"],
            message_thread_id=context.bot_data["game_topic_id"],
            text=moment.caption,
        )
    except TelegramError:
        logger.opt(exception=True).warning(
            "could not post the {moment} line either", moment=moment.kind.value, game_id=game_id
        )


async def post_charm_moment(
    context: ContextTypes.DEFAULT_TYPE, moment: CharmMoment, *, game_id: int
) -> None:
    """Draw and post the card; on any failure post the caption as plain text."""
    try:
        avatar = await fetch_avatar(context.bot, moment.user_id)
        photo = await asyncio.to_thread(_render, moment, avatar)
        await context.bot.send_photo(
            chat_id=context.bot_data["group_chat_id"],
            message_thread_id=context.bot_data["game_topic_id"],
            photo=photo,
            caption=moment.caption,
        )
    except (OSError, ValueError, TelegramError):
        logger.opt(exception=True).warning(
            "could not post the {moment} card — posting the line instead",
            moment=moment.kind.value,
            game_id=game_id,
        )
        await _send_text(context, moment, game_id)
        return
    logger.info("posted the {moment} card", moment=moment.kind.value, game_id=game_id)
