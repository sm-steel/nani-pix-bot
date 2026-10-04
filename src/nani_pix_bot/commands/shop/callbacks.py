"""Inline-button router for the DM clue shop (`shop:` callbacks). Text and
image clues are bought here, and a bought clue can be shared to the group."""

from dataclasses import dataclass

from loguru import logger
from sqlalchemy.orm import Session
from telegram import CallbackQuery, Update, User
from telegram.error import BadRequest, TelegramError
from telegram.ext import ContextTypes

from nani_pix_bot.commands.helpers.membership import is_group_member
from nani_pix_bot.commands.shop import images, share
from nani_pix_bot.commands.shop.deliver import (
    deliver_image_clue,
    deliver_text_clue,
    post_bought_notice,
    text_clue_message,
)
from nani_pix_bot.commands.shop.keyboards import (
    SHOP_BUY_PREFIX,
    SHOP_SHARE_PREFIX,
    SHOP_TILE_PREFIX,
    share_keyboard,
    tile_keyboard,
)
from nani_pix_bot.db import session_scope
from nani_pix_bot.models import CluePurchase
from nani_pix_bot.models.enums import ClueKind
from nani_pix_bot.models.game import Game
from nani_pix_bot.services import i18n, players, settings
from nani_pix_bot.services.clues import shop
from nani_pix_bot.services.clues.shop import (
    TEXT_KINDS,
    TILE_GRID,
    PurchaseRequest,
    Refusal,
    ShopRefusedError,
)

_LETTER_KINDS = frozenset({ClueKind.FIRST_LETTER, ClueKind.LAST_LETTER})
_BUY_PARTS = 4  # shop:buy:<game_id>:<kind>  and  shop:tile:<game_id>:<index>


@dataclass(frozen=True)
class _BuyTap:
    query: CallbackQuery
    user: User
    game_id: int
    kind: ClueKind
    tile_index: int | None = None


@dataclass(frozen=True)
class _Bought:
    """What a committed text-clue purchase needs for its Telegram sends."""

    lang: str
    purchase_id: int
    amount: int
    message: str
    followup: str | None


@dataclass(frozen=True)
class _ImageBought:
    """What a committed image-clue purchase needs for its Telegram sends."""

    lang: str
    purchase_id: int
    amount: int
    photo: bytes
    caption_key: str
    owned_tiles: frozenset[int] = frozenset()


def _parse_buy(data: str) -> tuple[int, ClueKind] | None:
    parts = data.split(":")
    if len(parts) != _BUY_PARTS:
        return None
    try:
        return int(parts[2]), ClueKind(parts[3])
    except ValueError:
        return None


def _parse_tile(data: str) -> tuple[int, int] | None:
    """(game_id, tile index) from `shop:tile:<gid>:<index>`; None unless
    the index is an integer inside the grid."""
    parts = data.split(":")
    if len(parts) != _BUY_PARTS:
        return None
    try:
        game_id, index = int(parts[2]), int(parts[3])
    except ValueError:
        return None
    return (game_id, index) if 0 <= index < TILE_GRID * TILE_GRID else None


async def _route_buy(context: ContextTypes.DEFAULT_TYPE, query: CallbackQuery, user: User) -> bool:
    parsed = _parse_buy(query.data or "")
    if parsed is None:
        return False
    tap = _BuyTap(query, user, *parsed)
    if tap.kind in TEXT_KINDS:
        await _buy_text(context, tap)
    elif tap.kind is ClueKind.SCREENSHOT:
        await _buy_screenshot(context, tap)
    elif tap.kind is ClueKind.TILE:
        await _open_tile_grid(context, tap)
    else:
        return False
    return True


async def _route_tile(context: ContextTypes.DEFAULT_TYPE, query: CallbackQuery, user: User) -> bool:
    parsed = _parse_tile(query.data or "")
    if parsed is None:
        return False
    await _buy_tile(context, _BuyTap(query, user, parsed[0], ClueKind.TILE, parsed[1]))
    return True


async def shop_callback_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    if query is None or query.data is None or query.from_user is None:
        return
    if query.data.startswith(SHOP_BUY_PREFIX):
        handled = await _route_buy(context, query, query.from_user)
    elif query.data.startswith(SHOP_TILE_PREFIX):
        handled = await _route_tile(context, query, query.from_user)
    elif query.data.startswith(SHOP_SHARE_PREFIX):
        handled = await share.route_share(context, query, query.from_user)
    else:
        handled = False
    if not handled:
        logger.warning("Ignoring shop callback {!r}", query.data)
        await query.answer()


def _language(context: ContextTypes.DEFAULT_TYPE) -> str:
    with session_scope(context.bot_data["session_factory"]) as session:
        return settings.get_language(session)


async def _member_ok(context: ContextTypes.DEFAULT_TYPE, tap: _BuyTap) -> bool:
    if await is_group_member(context.bot, context.bot_data["group_chat_id"], tap.user.id):
        return True
    logger.warning("Shop purchase refused for {}: not a group member", tap.user.id)
    await tap.query.answer(i18n.t("dm_start.not_a_member", _language(context)), show_alert=True)
    return False


async def _alert_refusal(
    context: ContextTypes.DEFAULT_TYPE, tap: _BuyTap, refused: ShopRefusedError
) -> None:
    text = _refusal_text(context.bot_data["session_factory"], tap, refused.refusal)
    await tap.query.answer(text, show_alert=True)


async def _buy_text(context: ContextTypes.DEFAULT_TYPE, tap: _BuyTap) -> None:
    if not await _member_ok(context, tap):
        return
    try:
        bought = _purchase(context.bot_data["session_factory"], tap)
    except ShopRefusedError as refused:
        await _alert_refusal(context, tap, refused)
        return
    await _deliver(context, tap, bought)


def _charge(
    session: Session, tap: _BuyTap, request: PurchaseRequest
) -> tuple[Game, CluePurchase, int]:
    """Charge and record `request` for the tapping player inside the
    caller's transaction; a refusal raises ShopRefusedError."""
    buyer = players.get_or_create_player(session, tap.user.id, username=tap.user.username)
    game = shop.active_game_for(session, tap.game_id)
    if game is None:
        raise ShopRefusedError(Refusal.NO_GAME)
    amount = shop.price(session, game, buyer, request.kind)
    return game, shop.purchase(session, game, buyer, request), amount


def _purchase(session_factory, tap: _BuyTap) -> _Bought:
    """One transaction: charge, record, and build the messages. A refusal
    raises ShopRefusedError out of the block, rolling everything back."""
    with session_scope(session_factory) as session:
        lang = settings.get_language(session)
        game, row, amount = _charge(session, tap, PurchaseRequest(tap.kind))
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


async def _fail_delivery(
    context: ContextTypes.DEFAULT_TYPE, tap: _BuyTap, bought: _Bought | _ImageBought
) -> None:
    """Refund a purchase whose clue Telegram would not take, and say so."""
    _refund(context.bot_data["session_factory"], bought.purchase_id)
    await tap.query.answer(
        i18n.t("shop.delivery_failed", bought.lang, amount=bought.amount), show_alert=True
    )


async def _announce(context: ContextTypes.DEFAULT_TYPE, tap: _BuyTap, lang: str) -> None:
    await tap.query.answer()
    await post_bought_notice(context, tap.user.full_name, tap.kind, lang)


async def _deliver(context: ContextTypes.DEFAULT_TYPE, tap: _BuyTap, bought: _Bought) -> None:
    """After the commit: DM the clue (refunding if that fails), then the
    shape follow-up and the topic notice."""
    markup = share_keyboard(bought.purchase_id, bought.lang)
    if not await deliver_text_clue(context, tap.user.id, bought.message, markup):
        await _fail_delivery(context, tap, bought)
        return
    if bought.followup is not None:
        await deliver_text_clue(context, tap.user.id, bought.followup)
    await _announce(context, tap, bought.lang)


def _refund(session_factory, purchase_id: int) -> None:
    with session_scope(session_factory) as session:
        row = session.get(CluePurchase, purchase_id)
        if row is None:
            logger.error("Cannot refund purchase {}: row is gone", purchase_id)
            return
        shop.refund(session, row)
    logger.error("Clue delivery failed; refunded purchase {}", purchase_id)


async def _buy_screenshot(context: ContextTypes.DEFAULT_TYPE, tap: _BuyTap) -> None:
    """Network first, charge second: nobody pays for a screenshot that
    could not be found, downloaded or pixelated."""
    session_factory = context.bot_data["session_factory"]
    if not await _member_ok(context, tap):
        return
    try:
        with session_scope(session_factory) as session:
            plan = images.screenshot_plan(session, tap.user, tap.game_id)
        fetched = await images.fetch_extra_screenshot(context, plan)
        if fetched is None:
            await tap.query.answer(i18n.t("shop.no_screenshot_left", plan.lang), show_alert=True)
            return
        with session_scope(session_factory) as session:
            request = PurchaseRequest(tap.kind, screenshot_url=fetched.url)
            _, row, amount = _charge(session, tap, request)
            bought = _ImageBought(
                plan.lang, row.id, amount, fetched.pixelated, "clue.screenshot_caption"
            )
    except ShopRefusedError as refused:
        await _alert_refusal(context, tap, refused)
        return
    if await _send_image(context, tap, bought):
        await _announce(context, tap, bought.lang)


async def _open_tile_grid(context: ContextTypes.DEFAULT_TYPE, tap: _BuyTap) -> None:
    """No charge: just show the 8x8 grid, ticking the tiles already owned."""
    if not await _member_ok(context, tap):
        return
    try:
        with session_scope(context.bot_data["session_factory"]) as session:
            _, lang, _ = images.offered_game(session, tap.user, tap.game_id, ClueKind.TILE)
            owned = shop.owned_tiles(session, tap.game_id, tap.user.id)
    except ShopRefusedError as refused:
        await _alert_refusal(context, tap, refused)
        return
    try:
        await context.bot.send_message(
            chat_id=tap.user.id,
            text=i18n.t("shop.tile_pick", lang),
            reply_markup=tile_keyboard(tap.game_id, owned),
        )
    except TelegramError:
        logger.exception("Could not DM the tile grid to {}", tap.user.id)
    await tap.query.answer()


async def _buy_tile(context: ContextTypes.DEFAULT_TYPE, tap: _BuyTap) -> None:
    if not await _member_ok(context, tap):
        return
    try:
        bought = _purchase_tile(context.bot_data["session_factory"], tap)
    except ShopRefusedError as refused:
        await _alert_refusal(context, tap, refused)
        return
    if not await _send_image(context, tap, bought):
        return
    await _refresh_grid(tap, bought.owned_tiles)
    await _announce(context, tap, bought.lang)


def _purchase_tile(session_factory, tap: _BuyTap) -> _ImageBought:
    """One transaction: charge, record, and render the reveal (it needs
    the deferred original image). A refusal rolls everything back."""
    with session_scope(session_factory) as session:
        lang = settings.get_language(session)
        request = PurchaseRequest(tap.kind, tile_index=tap.tile_index)
        game, row, amount = _charge(session, tap, request)
        owned = shop.owned_tiles(session, game.id, tap.user.id)
        photo = images.render_tile_clue(session, game, owned)
        return _ImageBought(lang, row.id, amount, photo, "clue.tile_caption", frozenset(owned))


async def _send_image(
    context: ContextTypes.DEFAULT_TYPE, tap: _BuyTap, bought: _ImageBought
) -> bool:
    """DM the picture; on failure refund and alert (False), on success
    remember its file_id for the share flow."""
    file_id = await deliver_image_clue(
        context,
        tap.user.id,
        bought.photo,
        i18n.t(bought.caption_key, bought.lang),
        share_keyboard(bought.purchase_id, bought.lang),
    )
    if file_id is None:
        await _fail_delivery(context, tap, bought)
        return False
    with session_scope(context.bot_data["session_factory"]) as session:
        row = session.get(CluePurchase, bought.purchase_id)
        if row is not None:
            row.telegram_file_id = file_id
    return True


async def _refresh_grid(tap: _BuyTap, owned: frozenset[int]) -> None:
    """Tick the new tile on the grid message the buyer tapped."""
    try:
        await tap.query.edit_message_reply_markup(
            reply_markup=tile_keyboard(tap.game_id, set(owned))
        )
    except BadRequest as error:
        if "not modified" not in str(error).lower():
            logger.warning("Could not refresh the tile grid: {}", error)
    except TelegramError:
        logger.warning("Could not refresh the tile grid", exc_info=True)
