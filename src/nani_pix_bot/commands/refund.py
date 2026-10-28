"""/refund [@user] — admin-only (DM) refund of one clue purchase, picked from a
paginated keyboard (without the @user, a menu of who has one comes first:
refund_picker.py). Runs the same shop.refund() a failed delivery uses, then
tells the player in DM (issue #249)."""

from loguru import logger
from sqlalchemy.orm import Session, sessionmaker
from telegram import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.error import TelegramError
from telegram.ext import ContextTypes

from nani_pix_bot.commands import refund_picker
from nani_pix_bot.commands.helpers.membership import is_group_admin
from nani_pix_bot.commands.helpers.scoping import is_private_chat
from nani_pix_bot.commands.refund_views import REFUND_PREFIX, kind_name, page_view, player_name
from nani_pix_bot.db import session_scope
from nani_pix_bot.models.clue_purchase import CluePurchase
from nani_pix_bot.models.enums import ClueKind
from nani_pix_bot.models.player import Player
from nani_pix_bot.services import i18n, players, settings
from nani_pix_bot.services.clues import shop


async def refund_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    message = update.message
    user = update.effective_user
    if not is_private_chat(update) or message is None or user is None:
        return
    session_factory = context.bot_data["session_factory"]
    with session_scope(session_factory) as session:
        lang = settings.get_language(session)
    if not await is_group_admin(context.bot, context.bot_data["group_chat_id"], user.id):
        logger.warning("non-admin tried /refund")
        await message.reply_text(i18n.t("commands.admins_only", lang))
        return
    args = context.args or []
    if not args:
        with session_scope(session_factory) as session:
            text, keyboard = refund_picker.open_view(session, user.id)
        await message.reply_text(text, reply_markup=keyboard)
        return
    if len(args) != 1:
        logger.info("sent /refund with bad args {args!r} — replied with usage", args=args)
        await message.reply_text(i18n.t("refund.usage", lang))
        return
    username = args[0].lstrip("@")
    with session_scope(session_factory) as session:
        player = players.find_player_by_username(session, username)
        if player is None:
            logger.warning(
                "tried /refund on unknown username {target_username!r}", target_username=username
            )
            await message.reply_text(i18n.t("refund.unknown_user", lang, username=username))
            return
        text, markup = page_view(session, player, 0, lang)
        logger.info(
            "opened /refund for {target}",
            target=players.describe_player_id(session, player.telegram_user_id),
            target_id=player.telegram_user_id,
        )
    await message.reply_text(text, reply_markup=markup)


async def refund_callback_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    if query is None or query.data is None or query.from_user is None:
        return
    await query.answer()
    session_factory = context.bot_data["session_factory"]
    # A Telegram call, so resolved before the transaction opens (see stop.py).
    is_admin = await is_group_admin(
        context.bot, context.bot_data["group_chat_id"], query.from_user.id
    )
    with session_scope(session_factory) as session:
        lang = settings.get_language(session)
    if not is_admin:
        logger.warning("non-admin tapped a /refund button")
        return
    action, _, rest = query.data.removeprefix(REFUND_PREFIX).partition(":")
    if action == "n":
        logger.info("canceled a /refund")
        await query.edit_message_text(i18n.t("refund.canceled", lang))
    elif action == "p":
        await _show_page(query, session_factory, rest, lang)
    elif action == "s":
        await _show_confirm(query, session_factory, int(rest), lang)
    elif action == "y":
        await _confirm(query, context, session_factory, int(rest), lang)


async def _show_page(
    query: CallbackQuery, session_factory: sessionmaker[Session], rest: str, lang: str
) -> None:
    player_id, _, page = rest.partition(":")
    with session_scope(session_factory) as session:
        player = session.get(Player, int(player_id))
        if player is None:
            return
        text, markup = page_view(session, player, int(page), lang)
    logger.info("paged /refund to page {page}", page=int(page) + 1)
    await query.edit_message_text(text, reply_markup=markup)


def _refundable_item(session: Session, row: CluePurchase) -> shop.RefundableItem | None:
    """The picker's entry for `row` (which still exists, so it is refundable)."""
    items = shop.refundable_purchases(session, row.player_id)
    return next((i for i in items if i.purchase_id == row.id), None)


async def _show_confirm(
    query: CallbackQuery, session_factory: sessionmaker[Session], purchase_id: int, lang: str
) -> None:
    with session_scope(session_factory) as session:
        row = session.get(CluePurchase, purchase_id)
        player = session.get(Player, row.player_id) if row else None
        item = _refundable_item(session, row) if row else None
        if player is None or item is None:
            logger.warning(
                "picked an already-refunded purchase {purchase_id} in /refund",
                purchase_id=purchase_id,
            )
            await query.edit_message_text(i18n.t("refund.stale", lang))
            return
        text = i18n.t(
            "refund.confirm_prompt",
            lang,
            amount=item.amount,
            username=player_name(player),
            kind=kind_name(item.kind, lang),
            game_id=item.game_id,
        )
        if item.cashback_deducted:
            text += i18n.t("refund.cashback_deducted", lang, deducted=item.cashback_deducted)
    markup = InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton(
                    i18n.t("refund.confirm", lang), callback_data=f"{REFUND_PREFIX}y:{purchase_id}"
                ),
                InlineKeyboardButton(
                    i18n.t("refund.cancel", lang), callback_data=f"{REFUND_PREFIX}n"
                ),
            ]
        ]
    )
    logger.info("picked purchase {purchase_id} in /refund — confirm shown", purchase_id=purchase_id)
    await query.edit_message_text(text, reply_markup=markup)


async def _confirm(
    query: CallbackQuery,
    context: ContextTypes.DEFAULT_TYPE,
    session_factory: sessionmaker[Session],
    purchase_id: int,
    lang: str,
) -> None:
    with session_scope(session_factory) as session:
        row = session.get(CluePurchase, purchase_id)
        player = session.get(Player, row.player_id) if row else None
        if row is None or player is None:
            logger.warning(
                "confirmed /refund of already-refunded purchase {purchase_id}",
                purchase_id=purchase_id,
            )
            await query.edit_message_text(i18n.t("refund.stale", lang))
            return
        kind, game_id, username = ClueKind(row.kind), row.game_id, player_name(player)
        player_id = player.telegram_user_id
        # shop.refund() logs the INFO line (amount, recipient, game).
        amount = shop.refund(session, row)
    dm_text = i18n.t(
        "refund.player_dm", lang, amount=amount, kind=kind_name(kind, lang), game_id=game_id
    )
    await _notify_player(context, player_id, dm_text)
    await query.edit_message_text(i18n.t("refund.done", lang, amount=amount, username=username))


async def _notify_player(context: ContextTypes.DEFAULT_TYPE, player_id: int, text: str) -> None:
    try:
        await context.bot.send_message(chat_id=player_id, text=text)
    except TelegramError as exc:
        logger.warning(
            "couldn't DM the refund to {recipient}: {error}",
            recipient=player_id,
            recipient_id=player_id,
            error=exc,
        )
