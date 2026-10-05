"""The free "share with the group" action: a player posts a clue they
bought to the game topic, once, while its round is active."""

import html
from dataclasses import dataclass

from loguru import logger
from telegram import CallbackQuery, User
from telegram.error import TelegramError
from telegram.ext import ContextTypes

from nani_pix_bot.commands.shop.deliver import share_clue, text_clue_message
from nani_pix_bot.commands.shop.keyboards import SHOP_SHARE_PREFIX
from nani_pix_bot.db import session_scope
from nani_pix_bot.models import CluePurchase
from nani_pix_bot.models.enums import ClueKind
from nani_pix_bot.services import i18n, settings
from nani_pix_bot.services.clues import shop
from nani_pix_bot.services.clues.shop import TEXT_KINDS


@dataclass(frozen=True)
class _SharePost:
    """What a committed share needs for its topic post: `text` is the HTML
    message, or the caption when `file_id` (an image clue) is set."""

    lang: str
    text: str
    file_id: str | None
    game_id: int


class _ShareRefusedError(Exception):
    """A share the tapping player may not make; `text` is the alert to show."""

    def __init__(self, text: str) -> None:
        super().__init__(text)
        self.text = text


def _prepare_share(session_factory, user: User, purchase_id: int) -> _SharePost:
    """One transaction: check the share is allowed, mark it shared and build
    the post. A refusal raises _ShareRefusedError out of the block, rolling
    the mark back."""
    with session_scope(session_factory) as session:
        lang = settings.get_language(session)
        row = session.get(CluePurchase, purchase_id)
        if row is None or row.player_id != user.id:
            logger.warning(
                "share of purchase {purchase_id} refused: not theirs", purchase_id=purchase_id
            )
            raise _ShareRefusedError(i18n.t("shop.share_not_yours", lang))
        game = shop.active_game_for(session, row.game_id)
        if game is None:
            logger.info(
                "tried to share purchase {purchase_id} after the round ended",
                purchase_id=purchase_id,
                game_id=row.game_id,
            )
            raise _ShareRefusedError(i18n.t("shop.stale", lang))
        kind = ClueKind(row.kind)
        is_image = kind not in TEXT_KINDS
        if is_image and row.telegram_file_id is None:
            logger.error(
                "purchase {purchase_id} has no stored file_id to share",
                purchase_id=purchase_id,
                game_id=game.id,
            )
            raise _ShareRefusedError(i18n.t("shop.share_failed", lang))
        if not shop.mark_shared(row):
            logger.info(
                "tried to share purchase {purchase_id} again — already shared",
                purchase_id=purchase_id,
                game_id=game.id,
            )
            raise _ShareRefusedError(i18n.t("shop.already_shared", lang))
        header = i18n.t("shop.shared", lang, name=html.escape(user.full_name))
        if is_image:
            return _SharePost(lang, header, row.telegram_file_id, game.id)
        owned = shop.owned_kinds(session, game.id, user.id)
        clue = text_clue_message(game, kind, owned, lang)
        return _SharePost(lang, f"{header}\n{clue}", None, game.id)


def _unshare(session_factory, purchase_id: int) -> None:
    """Undo the shared mark of a post Telegram refused, so it can be retried."""
    with session_scope(session_factory) as session:
        row = session.get(CluePurchase, purchase_id)
        if row is not None:
            row.shared_at = None


async def _share(
    context: ContextTypes.DEFAULT_TYPE, query: CallbackQuery, user: User, purchase_id: int
) -> None:
    """Mark first and commit, then post; a failed post un-marks so the player
    can tap again."""
    session_factory = context.bot_data["session_factory"]
    try:
        post = _prepare_share(session_factory, user, purchase_id)
    except _ShareRefusedError as refused:
        await query.answer(refused.text, show_alert=True)
        return
    if not await share_clue(context, post.text, post.file_id):
        logger.warning(
            "sharing purchase {purchase_id} failed — marked unshared again",
            purchase_id=purchase_id,
            game_id=post.game_id,
        )
        _unshare(session_factory, purchase_id)
        await query.answer(i18n.t("shop.share_failed", post.lang), show_alert=True)
        return
    logger.info(
        "shared clue purchase {purchase_id} with the group",
        purchase_id=purchase_id,
        game_id=post.game_id,
    )
    try:
        await query.edit_message_reply_markup(reply_markup=None)
    except TelegramError:
        logger.opt(exception=True).warning("could not remove the share button")
    await query.answer(i18n.t("shop.shared_ok", post.lang))


async def route_share(context: ContextTypes.DEFAULT_TYPE, query: CallbackQuery, user: User) -> bool:
    """Handle `shop:share:<purchase_id>`; False (the caller logs and answers
    silently) when the id is not an integer."""
    try:
        purchase_id = int((query.data or "").removeprefix(SHOP_SHARE_PREFIX))
    except ValueError:
        return False
    await _share(context, query, user, purchase_id)
    return True
