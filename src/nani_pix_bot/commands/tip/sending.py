"""Sending a tip, whether the recipient was typed as @username or picked from
the menu, and the reply either way."""

from dataclasses import dataclass

from loguru import logger
from sqlalchemy.orm import Session
from telegram import User
from telegram.ext import ContextTypes

from nani_pix_bot.db import session_scope
from nani_pix_bot.models.player import Player
from nani_pix_bot.services import i18n, players, settings, used_menus
from nani_pix_bot.services.cosmetics import flair
from nani_pix_bot.services.economy import tips


@dataclass(frozen=True)
class Menu:
    """The picker message a tap came from: it tips once (services/used_menus.py)."""

    chat_id: int
    message_id: int


@dataclass(frozen=True)
class TipRequest:
    amount: int
    username: str | None = None  # typed after /tip, without the @
    player_id: int | None = None  # picked from the menu
    menu: Menu | None = None  # the menu it was picked from


class StaleMenuError(Exception):
    """The menu already tipped, or names someone who is no player."""


def _unknown_user_reply(context: ContextTypes.DEFAULT_TYPE, username: str, lang: str) -> str:
    # bot_data["bot_username"] is absent until the first getMe answers (and in
    # tests); without it the "write to the bot" hint is dropped, as in /correct.
    bot_username = context.bot_data.get("bot_username")
    key = "tip.unknown_user_hint" if bot_username else "tip.unknown_user"
    return i18n.t(key, lang, username=username, bot_username=bot_username)


def _recipient(session: Session, request: TipRequest) -> Player | None:
    if request.username is not None:
        found = players.find_player_by_username(session, request.username)
        if found is None:
            logger.warning(
                "tried /tip to unknown username {recipient_username!r}",
                recipient_username=request.username,
            )
        return found
    found = None if request.player_id is None else session.get(Player, request.player_id)
    if found is None:  # only forged callback data gets here
        logger.warning(
            "tried /tip to {recipient_id}, who is no player", recipient_id=request.player_id
        )
        raise StaleMenuError
    return found


def _claim(session: Session, menu: Menu | None) -> None:
    if menu is not None and not used_menus.claim(session, menu.chat_id, menu.message_id):
        logger.warning("refused a second tap on a used /tip menu", message_id=menu.message_id)
        raise StaleMenuError


def _recipient_name(session: Session, recipient_id: int, request: TipRequest) -> str:
    """As typed for the @username form; the plain display name for a pick."""
    if request.username is not None:
        return f"@{request.username}"
    return players.display_name(session, recipient_id, unmasked=True)


def _attempt(
    session: Session, context: ContextTypes.DEFAULT_TYPE, user: User, request: TipRequest
) -> str:
    lang = settings.get_language(session)
    _claim(session, request.menu)
    recipient = _recipient(session, request)
    if recipient is None:
        return _unknown_user_reply(context, request.username or str(request.player_id), lang)
    recipient_id = recipient.telegram_user_id
    if recipient_id == context.bot.id:
        logger.warning("tried to /tip the bot")
        return i18n.t("tip.cannot_tip_bot", lang)
    sender = players.get_or_create_player(session, user.id, username=user.username)
    tips.tip(session, sender, recipient, request.amount)
    return i18n.t(
        "tip.done",
        lang,
        sender=flair.named(session, user.id, user.full_name),
        recipient=flair.named(
            session, recipient_id, _recipient_name(session, recipient_id, request)
        ),
        amount=request.amount,
    )


def tip_reply(context: ContextTypes.DEFAULT_TYPE, user: User, request: TipRequest) -> str:
    """Send the tip and say how it went: done, or why not. A tap on a menu that
    already tipped raises StaleMenuError, and nothing moves."""
    session_factory = context.bot_data["session_factory"]
    try:
        with session_scope(session_factory) as session:
            return _attempt(session, context, user, request)
    except tips.TipRefusedError as error:
        return refusal_reply(session_factory, user.id, error.refusal)


def refusal_reply(session_factory, user_id: int, refusal: tips.TipRefusal) -> str:
    """The reply for a refused tip; its transaction already rolled back, so
    the balance is read in a fresh scope."""
    logger.warning("/tip refused: {reason}", reason=refusal.value)
    with session_scope(session_factory) as session:
        lang = settings.get_language(session)
        player = session.get(Player, user_id)
        balance = player.currency if player is not None else 0
    return i18n.t(f"tip.refusal.{refusal.value}", lang, minimum=tips.TIP_MIN, balance=balance)
