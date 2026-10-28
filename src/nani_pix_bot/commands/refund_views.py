"""The /refund purchase list: one player's refundable clue purchases, a page
at a time, each a button that opens the confirm step (refund.py)."""

from sqlalchemy.orm import Session
from telegram import InlineKeyboardButton, InlineKeyboardMarkup

from nani_pix_bot.commands.helpers.player_picker import View
from nani_pix_bot.models.enums import ClueKind
from nani_pix_bot.models.player import Player
from nani_pix_bot.services import i18n
from nani_pix_bot.services.clues import shop

REFUND_PREFIX = "refund:"
REFUND_PAGE_SIZE = 5


def kind_name(kind: ClueKind, lang: str) -> str:
    return i18n.t(f"clue_kind.{kind.value}", lang)


def player_name(player: Player) -> str:
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
        kind=kind_name(item.kind, lang),
        amount=item.amount,
        date=item.created_at.strftime("%Y-%m-%d"),
    )
    return InlineKeyboardButton(label, callback_data=f"{REFUND_PREFIX}s:{item.purchase_id}")


def page_view(session: Session, player: Player, page: int, lang: str) -> View:
    items = shop.refundable_purchases(session, player.telegram_user_id)
    if not items:
        return i18n.t("refund.none", lang, username=player_name(player)), None
    pages = (len(items) + REFUND_PAGE_SIZE - 1) // REFUND_PAGE_SIZE
    page = max(0, min(page, pages - 1))
    shown = items[page * REFUND_PAGE_SIZE : (page + 1) * REFUND_PAGE_SIZE]
    rows = [[_item_button(item, lang)] for item in shown]
    nav = _nav_row(player.telegram_user_id, page, pages, lang)
    if nav:
        rows.append(nav)
    header = i18n.t(
        "refund.list_header", lang, username=player_name(player), page=page + 1, pages=pages
    )
    return header, InlineKeyboardMarkup(rows)
