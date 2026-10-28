"""/omikuji — draw today's fortune slip, in a DM or the game topic (Halloween).

The reply is the omikuji moment card (the drawer beside their slip, and what it
did) with the slip text as its caption; the text alone when the slip art is
missing or the card can't be drawn. A kyō prank drawn in a DM also posts the same
card to the game topic, captioned with the self-prank line: every hex is public."""

from dataclasses import dataclass
from datetime import UTC, datetime

from loguru import logger
from sqlalchemy.orm import Session
from telegram import Message, Update, User
from telegram.error import TelegramError
from telegram.ext import ContextTypes

from nani_pix_bot.commands.helpers.membership import is_group_member
from nani_pix_bot.commands.helpers.scoping import is_game_topic, is_private_chat
from nani_pix_bot.db import session_scope
from nani_pix_bot.jobs.moments import OmikujiMoment, draw_omikuji_card, post_omikuji_card
from nani_pix_bot.models.halloween import Hex
from nani_pix_bot.seasons.mechanics import OmikujiKind
from nani_pix_bot.services import i18n, players, settings
from nani_pix_bot.services.cosmetics import flair
from nani_pix_bot.services.seasons import omikuji
from nani_pix_bot.services.seasons.omikuji import Draw, OmikujiRefusedError


@dataclass(frozen=True)
class _Card:
    """The card's words, worked out inside the session; the slip art is read after."""

    run_id: str
    kind: OmikujiKind
    user_id: int
    name: str  # the drawer's @username, unmasked
    title: str
    detail: str


@dataclass(frozen=True)
class _Reply:
    """What /omikuji sends once its session has committed."""

    text: str
    card: _Card | None = None  # None: refused, so the reply is text only
    public: str | None = None  # the topic's caption, when a prank landed
    hex_kind: str | None = None


def _hours(prank: Hex) -> int:
    if prank.expires_at is None:
        return 0
    return round((prank.expires_at - prank.cast_at).total_seconds() / 3600)


def caption(draw: Draw, name: str, lang: str) -> str:
    """The slip's lines: the outcome, then what it paid, gave and did to `name`."""
    outcome = draw.outcome
    lines = [i18n.t(f"omikuji.slip.{outcome.kind.value}", lang, name=name)]
    if outcome.coins > 0:
        lines.append(i18n.t("omikuji.coins", lang, coins=outcome.coins))
    if outcome.consumable is not None:
        lines.append(i18n.t(f"omikuji.gift.{outcome.consumable.value}", lang))
    if draw.prank is not None:
        hours = _hours(draw.prank)
        lines.append(i18n.t(f"omikuji.prank.{draw.prank.kind}", lang, hours=hours))
    if draw.spared:
        lines.append(i18n.t("omikuji.prank_spared", lang))
    return "\n".join(lines)


def card_title(draw: Draw, name: str, lang: str) -> str:
    return i18n.t(f"omikuji.card_title.{draw.outcome.kind.value}", lang, name=name)


def _gift_detail(draw: Draw, name: str, lang: str) -> str:
    """What a slip without a prank gave: coins, a charm, both, or nothing."""
    coins, consumable = draw.outcome.coins, draw.outcome.consumable
    if consumable is None:
        key = "omikuji.card_detail.coins" if coins > 0 else "omikuji.card_detail.nothing"
        return i18n.t(key, lang, coins=coins, name=name)
    gift = i18n.t(f"omikuji.card_gift.{consumable.value}", lang)
    if coins > 0:
        return i18n.t("omikuji.card_detail.coins_gift", lang, coins=coins, gift=gift, name=name)
    # the gift opens the line here, so it takes a capital
    return i18n.t("omikuji.card_detail.gift", lang, gift=gift[:1].upper() + gift[1:], name=name)


def card_detail(draw: Draw, name: str, lang: str) -> str:
    """The card's effect line: what the slip did to `name`. No emoji: the card
    font has none."""
    if draw.prank is not None:
        kind = draw.prank.kind
        return i18n.t(f"hex.card_detail.{kind}", lang, target=name, hours=_hours(draw.prank))
    if draw.spared:
        return i18n.t("omikuji.card_detail.spared", lang, name=name)
    return _gift_detail(draw, name, lang)


def _drawn_reply(session: Session, user_id: int, drawn: Draw, slip_name: str, lang: str) -> _Reply:
    # A prank landing now is the one moment the drawer is named unmasked (like a
    # cast notice); otherwise a Nopperabō they're already under masks the card too.
    if drawn.prank is not None:
        name = players.display_name(session, user_id, unmasked=True)
    else:
        name = players.image_name(session, user_id, lang)
    card = _Card(
        drawn.run.run_id,
        drawn.outcome.kind,
        user_id,
        name,
        card_title(drawn, name, lang),
        card_detail(drawn, name, lang),
    )
    text = caption(drawn, slip_name, lang)
    prank = drawn.prank
    if prank is None:
        return _Reply(text, card)
    public = i18n.t(f"hex.notice.self_prank.{prank.kind}", lang, target=name, hours=_hours(prank))
    return _Reply(text, card, public, prank.kind)


def _draw(session: Session, user: User) -> _Reply:
    lang = settings.get_language(session)
    now = datetime.now(UTC)
    player = players.get_or_create_player(session, user.id, username=user.username)
    # named before the draw: the slip shows the drawer as they walked up to the shrine
    name = flair.named(session, user.id, players.display_name(session, user.id), now)
    try:
        drawn = omikuji.draw(session, player, now)
    except OmikujiRefusedError as refused:
        return _Reply(i18n.t(f"omikuji.{refused.refusal.value}", lang))
    return _drawn_reply(session, user.id, drawn, name, lang)


async def _draw_card(context: ContextTypes.DEFAULT_TYPE, card: _Card | None) -> bytes | None:
    """The card's JPEG, or None when there is no slip art or it can't be drawn."""
    slip = None if card is None else omikuji.slip_png(card.run_id, card.kind)
    if card is None or slip is None:
        return None
    moment = OmikujiMoment(card.user_id, card.name, slip, card.title, card.detail)
    return await draw_omikuji_card(context, moment)


async def _send_reply(message: Message, text: str, card: bytes | None) -> None:
    if card is not None:
        try:
            await message.reply_photo(photo=card, caption=text)
            return
        except TelegramError as error:
            logger.warning(
                "could not send the omikuji card — sending its text: {error}", error=str(error)
            )
    await message.reply_text(text)


def _where(update: Update, context: ContextTypes.DEFAULT_TYPE) -> str | None:
    """ "dm" or "topic"; None anywhere else, where /omikuji is ignored."""
    if is_private_chat(update):
        return "dm"
    in_topic = is_game_topic(
        update,
        group_chat_id=context.bot_data["group_chat_id"],
        game_topic_id=context.bot_data["game_topic_id"],
    )
    return "topic" if in_topic else None


async def omikuji_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    message, user = update.message, update.effective_user
    where = _where(update, context)
    if message is None or user is None or where is None:
        return
    factory = context.bot_data["session_factory"]
    if where == "dm" and not await is_group_member(
        context.bot, context.bot_data["group_chat_id"], user.id
    ):
        with session_scope(factory) as session:
            lang = settings.get_language(session)
        logger.warning("omikuji refused: not a group member")
        await message.reply_text(i18n.t("dm_start.not_a_member", lang))
        return
    logger.info("drew /omikuji in {where}", where=where)
    with session_scope(factory) as session:
        reply = _draw(session, user)
    card = await _draw_card(context, reply.card)
    try:
        await _send_reply(message, reply.text, card)
    except TelegramError:
        # the draw (and any hex) is committed: the topic still hears of the prank
        logger.opt(exception=True).warning("could not send the omikuji slip reply")
    # in the topic the reply itself is public; from a DM a prank goes to the topic too
    if where == "dm" and reply.public is not None:
        await post_omikuji_card(context, card, reply.public, hex_kind=reply.hex_kind)
