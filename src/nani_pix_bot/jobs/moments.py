"""Posting a moment card (Halloween) to the game topic: a charm used in a round, or a
hex cast at someone, as a picture with the plain line as its caption. A card that
can't be drawn or sent never blocks anything: the caption goes out as text instead."""

import asyncio
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass

from loguru import logger
from PIL import Image
from telegram.error import TelegramError
from telegram.ext import ContextTypes

from nani_pix_bot.jobs.avatars import fetch_avatar
from nani_pix_bot.models.enums import ConsumableKind
from nani_pix_bot.services.cards import moments as moment_cards
from nani_pix_bot.services.cards.moments import Caption, Duel, Face, Mood
from nani_pix_bot.services.cosmetics import consumable_card


@dataclass(frozen=True)
class CharmMoment:
    kind: ConsumableKind  # whose art the card shows
    user_id: int
    display_name: str  # the name pill: plain, no flair
    title: str
    detail: str  # the effect line under the title: what happens to the player
    caption: str  # the line the plain text would have been


@dataclass(frozen=True)
class Person:
    user_id: int
    display_name: str  # the name pill: plain, no flair or marker


@dataclass(frozen=True)
class DuelMoment:
    """A hex cast: the caster's art flies at the target. With `shield`, the target's
    Omamori blocked it: the blocked layout, `art` torn, `shield` beside the target."""

    name: str  # what the log calls it, e.g. "hex kappa"
    actor: Person
    target: Person
    art: Image.Image | None  # None: the art is missing, so the plain line goes out
    title: str
    detail: str
    caption: str
    shield: Image.Image | None = None
    blocked: bool = False


@dataclass(frozen=True)
class _Post:
    draw: Callable[[], Awaitable[bytes]]
    caption: str
    log: Mapping[str, object]  # `moment` names it; the rest are extra fields


def _render(moment: CharmMoment, avatar: bytes | None) -> bytes:
    face = Face(avatar, moment.display_name, moment.user_id)
    caption = Caption(moment.title, moment.detail)
    return moment_cards.charm_card(Mood.CHARM, face, consumable_card.art(moment.kind), caption)


def _render_duel(moment: DuelMoment, avatars: list[bytes | None]) -> bytes:
    actor = Face(avatars[0], moment.actor.display_name, moment.actor.user_id)
    target = Face(avatars[1], moment.target.display_name, moment.target.user_id)
    duel = Duel(actor, target)
    if moment.art is None:
        raise FileNotFoundError(f"no art for the {moment.name} card")
    caption = Caption(moment.title, moment.detail)
    if not moment.blocked:
        return moment_cards.duel_card(Mood.CURSE, duel, moment.art, caption)
    if moment.shield is None:
        raise FileNotFoundError(f"no shield art for the {moment.name} card")
    return moment_cards.blocked_duel_card(Mood.CHARM, duel, moment.art, moment.shield, caption)


async def _send_text(context: ContextTypes.DEFAULT_TYPE, post: _Post) -> bool:
    try:
        await context.bot.send_message(
            chat_id=context.bot_data["group_chat_id"],
            message_thread_id=context.bot_data["game_topic_id"],
            text=post.caption,
        )
    except TelegramError:
        logger.opt(exception=True).warning("could not post the {moment} line either", **post.log)
        return False
    return True


async def _post(context: ContextTypes.DEFAULT_TYPE, post: _Post) -> bool:
    """Draw and post the card; on any failure post the caption as plain text.
    True when either reached the topic."""
    try:
        photo = await post.draw()
        await context.bot.send_photo(
            chat_id=context.bot_data["group_chat_id"],
            message_thread_id=context.bot_data["game_topic_id"],
            photo=photo,
            caption=post.caption,
        )
    except (OSError, ValueError, TelegramError):
        logger.opt(exception=True).warning(
            "could not post the {moment} card — posting the line instead", **post.log
        )
        return await _send_text(context, post)
    logger.info("posted the {moment} card", **post.log)
    return True


async def post_charm_moment(
    context: ContextTypes.DEFAULT_TYPE, moment: CharmMoment, *, game_id: int
) -> None:
    """Draw and post the card; on any failure post the caption as plain text."""

    async def draw() -> bytes:
        avatar = await fetch_avatar(context.bot, moment.user_id)
        return await asyncio.to_thread(_render, moment, avatar)

    log = {"moment": moment.kind.value, "game_id": game_id}
    await _post(context, _Post(draw, moment.caption, log))


async def post_duel_moment(
    context: ContextTypes.DEFAULT_TYPE, moment: DuelMoment, **log: object
) -> bool:
    """Draw and post a duel card, with both players' avatars; on any failure post
    the caption as plain text. True when either reached the topic."""

    async def draw() -> bytes:
        avatars = await asyncio.gather(
            fetch_avatar(context.bot, moment.actor.user_id),
            fetch_avatar(context.bot, moment.target.user_id),
        )
        return await asyncio.to_thread(_render_duel, moment, list(avatars))

    return await _post(context, _Post(draw, moment.caption, {"moment": moment.name, **log}))
