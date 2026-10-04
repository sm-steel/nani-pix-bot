"""Inline-button router for the DM clue shop (`shop:` callbacks). Text clues
are bought here; the screenshot, tile and share branches are still stubs."""

from dataclasses import dataclass

from loguru import logger
from telegram import CallbackQuery, Update, User
from telegram.ext import ContextTypes

from nani_pix_bot.commands.helpers.membership import is_group_member
from nani_pix_bot.commands.shop.deliver import (
    deliver_text_clue,
    post_bought_notice,
    text_clue_message,
)
from nani_pix_bot.commands.shop.keyboards import SHOP_BUY_PREFIX, share_keyboard
from nani_pix_bot.db import session_scope
from nani_pix_bot.models import CluePurchase
from nani_pix_bot.models.enums import ClueKind
from nani_pix_bot.services import i18n, players, settings
from nani_pix_bot.services.clues import shop
from nani_pix_bot.services.clues.shop import TEXT_KINDS, PurchaseRequest, Refusal, ShopRefusedError

_LETTER_KINDS = frozenset({ClueKind.FIRST_LETTER, ClueKind.LAST_LETTER})
_BUY_PARTS = 4  # shop:buy:<game_id>:<kind>


@dataclass(frozen=True)
class _BuyTap:
    query: CallbackQuery
    user: User
    game_id: int
    kind: ClueKind


@dataclass(frozen=True)
class _Bought:
    """What a committed text-clue purchase needs for its Telegram sends."""

    lang: str
    purchase_id: int
    amount: int
    message: str
    followup: str | None


def _parse_buy(data: str) -> tuple[int, ClueKind] | None:
    parts = data.split(":")
    if len(parts) != _BUY_PARTS:
        return None
    try:
        return int(parts[2]), ClueKind(parts[3])
    except ValueError:
        return None


async def shop_callback_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    if query is None or query.data is None or query.from_user is None:
        return
    if query.data.startswith(SHOP_BUY_PREFIX):
        parsed = _parse_buy(query.data)
        if parsed is not None and parsed[1] in TEXT_KINDS:
            await _buy_text(context, _BuyTap(query, query.from_user, *parsed))
            return
    logger.warning("Ignoring shop callback {!r}", query.data)
    await query.answer()


def _language(context: ContextTypes.DEFAULT_TYPE) -> str:
    with session_scope(context.bot_data["session_factory"]) as session:
        return settings.get_language(session)


async def _buy_text(context: ContextTypes.DEFAULT_TYPE, tap: _BuyTap) -> None:
    session_factory = context.bot_data["session_factory"]
    if not await is_group_member(context.bot, context.bot_data["group_chat_id"], tap.user.id):
        logger.warning("Shop purchase refused for {}: not a group member", tap.user.id)
        await tap.query.answer(i18n.t("dm_start.not_a_member", _language(context)), show_alert=True)
        return
    try:
        bought = _purchase(session_factory, tap)
    except ShopRefusedError as refused:
        await tap.query.answer(
            _refusal_text(session_factory, tap, refused.refusal), show_alert=True
        )
        return
    await _deliver(context, tap, bought)


def _purchase(session_factory, tap: _BuyTap) -> _Bought:
    """One transaction: charge, record, and build the messages. A refusal
    raises ShopRefusedError out of the block, rolling everything back."""
    with session_scope(session_factory) as session:
        lang = settings.get_language(session)
        buyer = players.get_or_create_player(session, tap.user.id, username=tap.user.username)
        game = shop.active_game_for(session, tap.game_id)
        if game is None:
            raise ShopRefusedError(Refusal.NO_GAME)
        amount = shop.price(session, game, buyer, tap.kind)
        row = shop.purchase(session, game, buyer, PurchaseRequest(tap.kind))
        owned = shop.owned_kinds(session, game.id, tap.user.id)
        followup = None
        if tap.kind in _LETTER_KINDS and ClueKind.TITLE_SHAPE in owned:
            followup = text_clue_message(game, ClueKind.TITLE_SHAPE, owned, lang)
        return _Bought(
            lang=lang,
            purchase_id=row.id,
            amount=amount,
            message=text_clue_message(game, tap.kind, owned, lang),
            followup=followup,
        )


def _refusal_text(session_factory, tap: _BuyTap, refusal: Refusal) -> str:
    with session_scope(session_factory) as session:
        lang = settings.get_language(session)
        if refusal is Refusal.NO_GAME:
            return i18n.t("shop.stale", lang)
        buyer = players.get_or_create_player(session, tap.user.id, username=tap.user.username)
        return i18n.t(f"shop.refusal.{refusal.value}", lang, balance=buyer.currency)


async def _deliver(context: ContextTypes.DEFAULT_TYPE, tap: _BuyTap, bought: _Bought) -> None:
    """After the commit: DM the clue (refunding if that fails), then the
    shape follow-up and the topic notice."""
    markup = share_keyboard(bought.purchase_id, bought.lang)
    if not await deliver_text_clue(context, tap.user.id, bought.message, markup):
        _refund(context.bot_data["session_factory"], bought.purchase_id)
        await tap.query.answer(
            i18n.t("shop.delivery_failed", bought.lang, amount=bought.amount), show_alert=True
        )
        return
    if bought.followup is not None:
        await deliver_text_clue(context, tap.user.id, bought.followup)
    await tap.query.answer()
    await post_bought_notice(context, tap.user.full_name, tap.kind, bought.lang)


def _refund(session_factory, purchase_id: int) -> None:
    with session_scope(session_factory) as session:
        row = session.get(CluePurchase, purchase_id)
        if row is None:
            logger.error("Cannot refund purchase {}: row is gone", purchase_id)
            return
        shop.refund(session, row)
    logger.error("Clue delivery failed; refunded purchase {}", purchase_id)
