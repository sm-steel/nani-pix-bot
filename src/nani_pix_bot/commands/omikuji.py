"""/omikuji — draw today's fortune slip, in a DM or the game topic (Halloween).

The reply is the slip's picture with its caption (the caption alone when the art
is missing). A kyō prank also posts a Kyō card to the game topic, wherever the
slip was drawn: every hex is public."""

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
from nani_pix_bot.jobs.moments import KyoMoment, post_kyo_moment
from nani_pix_bot.models.halloween import Hex
from nani_pix_bot.services import i18n, players, settings
from nani_pix_bot.services.cosmetics import flair
from nani_pix_bot.services.seasons import hex_art, omikuji
from nani_pix_bot.services.seasons.omikuji import Draw, OmikujiRefusedError


@dataclass(frozen=True)
class _Reply:
    """What /omikuji sends once its session has committed."""

    text: str
    drawn: Draw | None  # None: refused, so the reply is text only
    kyo: KyoMoment | None  # the card for the topic, when a prank landed


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


def _kyo_moment(session: Session, prank: Hex, lang: str) -> KyoMoment:
    """The Kyō card: the hex has just landed, so like a cast notice it names the
    drawer unmasked, even under the Nopperabō it gave them."""
    name = players.display_name(session, prank.target_id, unmasked=True)
    hours = _hours(prank)
    return KyoMoment(
        kind=prank.kind,
        user_id=prank.target_id,
        card_name=name,
        art=hex_art.art(prank.kind),
        title=i18n.t("omikuji.card_title", lang, target=name),
        detail=i18n.t(f"hex.card_detail.{prank.kind}", lang, target=name, hours=hours),
        caption=i18n.t(f"hex.notice.self_prank.{prank.kind}", lang, target=name, hours=hours),
    )


def _draw(session: Session, user: User) -> _Reply:
    lang = settings.get_language(session)
    now = datetime.now(UTC)
    player = players.get_or_create_player(session, user.id, username=user.username)
    # named before the draw: the slip shows the drawer as they walked up to the shrine
    name = flair.named(session, user.id, players.display_name(session, user.id), now)
    try:
        drawn = omikuji.draw(session, player, now)
    except OmikujiRefusedError as refused:
        return _Reply(i18n.t(f"omikuji.{refused.refusal.value}", lang), None, None)
    kyo = None if drawn.prank is None else _kyo_moment(session, drawn.prank, lang)
    return _Reply(caption(drawn, name, lang), drawn, kyo)


async def _send_slip(message: Message, reply: _Reply) -> None:
    drawn = reply.drawn
    slip = None if drawn is None else omikuji.slip_png(drawn.run.run_id, drawn.outcome.kind)
    if slip is not None:
        try:
            await message.reply_photo(photo=slip, caption=reply.text)
            return
        except TelegramError as error:
            logger.warning(
                "could not send the omikuji slip photo — sending its text: {error}",
                error=str(error),
            )
    await message.reply_text(reply.text)


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
    try:
        await _send_slip(message, reply)
    except TelegramError:
        # the draw (and any hex) is committed: the Kyō card still goes out
        logger.opt(exception=True).warning("could not send the omikuji slip reply")
    if reply.kyo is not None:
        await post_kyo_moment(context, reply.kyo, hex_kind=reply.kyo.kind)
