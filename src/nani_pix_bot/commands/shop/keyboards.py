"""Inline keyboards for the DM clue shop."""

from telegram import InlineKeyboardButton, InlineKeyboardMarkup

from nani_pix_bot.models.enums import ClueKind
from nani_pix_bot.services import i18n
from nani_pix_bot.services.clues.shop import TILE_GRID, Offer

SHOP_PREFIX = "shop:"
SHOP_BUY_PREFIX = "shop:buy:"
SHOP_TILE_PREFIX = "shop:tile:"
SHOP_SHARE_PREFIX = "shop:share:"


def _offer_label(offer: Offer, lang: str) -> str:
    label = i18n.t(f"shop.item.{offer.kind.value}", lang, price=offer.price)
    return label if offer.affordable else i18n.t("shop.locked", lang, label=label)


def shop_keyboard(offers: list[Offer], game_id: int, lang: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [InlineKeyboardButton(_offer_label(o, lang), callback_data=_buy_data(game_id, o))]
            for o in offers
        ]
    )


def _buy_data(game_id: int, offer: Offer) -> str:
    data = f"{SHOP_BUY_PREFIX}{game_id}:{offer.kind.value}"
    if offer.kind is ClueKind.SCREENSHOT:
        # The owned count lets the callback refuse a stale second tap.
        data += f":{offer.owned_screenshots}"
    return data


def tile_keyboard(game_id: int, owned: set[int]) -> InlineKeyboardMarkup:
    def _cell(index: int) -> InlineKeyboardButton:
        return InlineKeyboardButton(
            "✅" if index in owned else "⬜", callback_data=f"{SHOP_TILE_PREFIX}{game_id}:{index}"
        )

    return InlineKeyboardMarkup(
        [[_cell(row * TILE_GRID + col) for col in range(TILE_GRID)] for row in range(TILE_GRID)]
    )


def share_keyboard(purchase_id: int, lang: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton(
                    i18n.t("shop.share_button", lang),
                    callback_data=f"{SHOP_SHARE_PREFIX}{purchase_id}",
                )
            ]
        ]
    )
