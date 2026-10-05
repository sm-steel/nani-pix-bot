"""/refund @user — admin-only (DM) refund of one clue purchase, picked from a
paginated keyboard. Runs the same shop.refund() a failed delivery uses, then
tells the player in DM (issue #249)."""

from loguru import logger
from sqlalchemy.orm import Session, sessionmaker
from telegram import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.error import TelegramError
from telegram.ext import ContextTypes

from nani_pix_bot.commands.helpers.membership import is_group_admin
from nani_pix_bot.commands.helpers.scoping import is_private_chat
from nani_pix_bot.db import session_scope
from nani_pix_bot.models.clue_purchase import CluePurchase
from nani_pix_bot.models.enums import ClueKind
from nani_pix_bot.models.player import Player
from nani_pix_bot.services import i18n, players, settings
from nani_pix_bot.services.clues import shop

REFUND_PREFIX = "refund:"
REFUND_PAGE_SIZE = 5


def _kind(kind: ClueKind, lang: str) -> str:
    return i18n.t(f"clue_kind.{kind.value}", lang)


def _username(player: Player) -> str:
    return player.username or str(player.telegram_user_id)


def _nav_row(player_id: int, page: int, pages: int, lang: str) -> list[InlineKeyboardButton]:
    targets = [
        ("refund.prev", page - 1, page > 0),
        ("refund.next", page + 1, page < pages - 1),
    ]
    return [
        InlineKeyboardButton(i18n.t(key, lang), callback_data=f"{REFUND_PREFIX}p:{player_id}:{to}")
        for key, to, shown in targets
        if shown
    ]


def _item_button(item: shop.RefundableItem, lang: str) -> InlineKeyboardButton:
    label = i18n.t(
        "refund.item",
        lang,
        game_id=item.game_id,
        kind=_kind(item.kind, lang),
        amount=item.amount,
        date=item.created_at.strftime("%Y-%m-%d"),
    )
    return InlineKeyboardButton(label, callback_data=f"{REFUND_PREFIX}s:{item.purchase_id}")


def _page_view(
    session: Session, player: Player, page: int, lang: str
) -> tuple[str, InlineKeyboardMarkup | None]:
    items = shop.refundable_purchases(session, player.telegram_user_id)
    if not items:
        return i18n.t("refund.none", lang, username=_username(player)), None
    pages = (len(items) + REFUND_PAGE_SIZE - 1) // REFUND_PAGE_SIZE
    page = max(0, min(page, pages - 1))
    shown = items[page * REFUND_PAGE_SIZE : (page + 1) * REFUND_PAGE_SIZE]
    rows = [[_item_button(item, lang)] for item in shown]
    nav = _nav_row(player.telegram_user_id, page, pages, lang)
    if nav:
        rows.append(nav)
    header = i18n.t(
        "refund.list_header", lang, username=_username(player), page=page + 1, pages=pages
    )
    return header, InlineKeyboardMarkup(rows)


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
        text, markup = _page_view(session, player, 0, lang)
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
        text, markup = _page_view(session, player, int(page), lang)
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
            username=_username(player),
            kind=_kind(item.kind, lang),
            game_id=item.game_id,
        )
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
        kind, game_id, username = ClueKind(row.kind), row.game_id, _username(player)
        player_id = player.telegram_user_id
        # shop.refund() logs the INFO line (amount, recipient, game).
        amount = shop.refund(session, row)
    dm_text = i18n.t(
        "refund.player_dm", lang, amount=amount, kind=_kind(kind, lang), game_id=game_id
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
