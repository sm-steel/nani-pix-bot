"""/seasonshop — what a player can buy with this season's 🪙, in a DM (seasons spec §4).
Tapping an offer shows what it looks like first; buying is a second tap."""

from dataclasses import dataclass
from types import MappingProxyType

from loguru import logger
from sqlalchemy.orm import Session
from telegram import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Update, User
from telegram.ext import ContextTypes

from nani_pix_bot.commands.achievements.common import MAX_ID, outsider_refusal
from nani_pix_bot.commands.helpers import item_preview
from nani_pix_bot.commands.helpers.dm_view import DmCommand, reply_with_view
from nani_pix_bot.db import session_scope
from nani_pix_bot.models.enums import SeasonStatus
from nani_pix_bot.models.season import SeasonSchedule
from nani_pix_bot.seasons import registry
from nani_pix_bot.seasons.definition import RewardKind, SeasonRun
from nani_pix_bot.seasons.registry import ResolvedItem
from nani_pix_bot.services import i18n, players, settings
from nani_pix_bot.services.cosmetics import clue_token_icon
from nani_pix_bot.services.economy import wallet
from nani_pix_bot.services.seasons import schedule, season_shop
from nani_pix_bot.services.seasons.season_shop import ShopRefusal, ShopRefusedError, Stock

PREFIX = "sshop:"
CLUE_ICON_KEY = "consumable/clue_token"


def _button(
    text: str, action: str, season_id: int, index: int | None = None
) -> InlineKeyboardButton:
    tail = f":{index}" if index is not None else ""
    return InlineKeyboardButton(text, callback_data=f"{PREFIX}{action}:{season_id}{tail}")


def _open(
    session: Session, season_id: int | None = None
) -> tuple[SeasonSchedule, SeasonRun] | None:
    """The running season and its run — and, given a tapped id, only when it is that season."""
    season = schedule.current(session)
    run = registry.get(season.run_id) if season is not None else None
    if season is None or run is None or season.status != SeasonStatus.ACTIVE:
        return None
    if season_id is not None and season.id != season_id:
        return None
    return season, run


def _resolved(row: Stock) -> ResolvedItem | None:
    return registry.item(row.item_id) if row.item_id is not None else None


def _name(row: Stock, lang: str) -> str:
    resolved = _resolved(row)
    if resolved is None:
        return i18n.t(f"season_pass.reward.{row.offer.kind.value}", lang)
    return resolved.item.name(lang)


def _kind(row: Stock, lang: str) -> str:
    resolved = _resolved(row)
    if resolved is None:
        return i18n.t("season_shop.consumable", lang)
    return i18n.t(f"cosmetics.slot.{resolved.item.slot.value}", lang)


def _mark(row: Stock, lang: str) -> str:
    if row.owned:
        return "✅ "
    if row.left == 0:
        return f"({i18n.t('season_shop.sold_out', lang)}) "
    return ""


def _line(row: Stock, lang: str) -> str:
    line = i18n.t(
        "season_shop.line",
        lang,
        mark=_mark(row, lang),
        name=_name(row, lang),
        price=row.offer.price,
    )
    if row.item_id is None and row.left > 0:
        line += f" ({i18n.t('season_shop.left', lang, left=row.left)})"
    return line


def _menu(
    session: Session, season: SeasonSchedule, run: SeasonRun, player_id: int, lang: str
) -> tuple[str, InlineKeyboardMarkup]:
    rows = season_shop.stock(session, season, run, player_id)
    balance = wallet.balance(session, player_id, season_id=season.id)
    header = i18n.t("season_shop.header", lang, season=run.name(lang), balance=balance)
    text = "\n".join([header, "", *(_line(row, lang) for row in rows)])
    buttons = [[_button(_name(row, lang), "i", season.id, i)] for i, row in enumerate(rows)]
    return text, InlineKeyboardMarkup(buttons)


def view(session: Session, player_id: int, lang: str) -> tuple[str, InlineKeyboardMarkup | None]:
    """The shop menu for the running season, or the closed notice."""
    open_run = _open(session)
    if open_run is None:
        return i18n.t("season_shop.closed", lang), None
    return _menu(session, *open_run, player_id, lang)


COMMAND = DmCommand("/seasonshop", "season_shop.dm_only", view)


async def season_shop_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await reply_with_view(update, context, COMMAND)


# --- taps ---


@dataclass(frozen=True)
class _Offer:
    text: str
    keyboard: InlineKeyboardMarkup
    resolved: ResolvedItem | None


def _offer(
    session: Session, season_id: int, player_id: int, index: int, lang: str
) -> _Offer | ShopRefusal | None:
    """The offer's page; UNKNOWN for a missing offer; None when the season isn't the open one."""
    open_run = _open(session, season_id)
    if open_run is None:
        return None
    season, run = open_run
    rows = season_shop.stock(session, season, run, player_id)
    if not 0 <= index < len(rows):
        return ShopRefusal.UNKNOWN
    row = rows[index]
    balance = wallet.balance(session, player_id, season_id=season.id)
    text = i18n.t(
        "season_shop.offer",
        lang,
        name=_name(row, lang),
        kind=_kind(row, lang),
        price=row.offer.price,
        balance=balance,
    )
    if row.offer.kind is RewardKind.CLUE_TICKET:
        text += "\n" + i18n.t("season_shop.offer_token_note", lang)
    buttons = []
    if row.left > 0:
        buy_label = i18n.t("season_shop.buy", lang, price=row.offer.price)
        buttons.append(_button(buy_label, "b", season_id, index))
    buttons.append(_button(i18n.t("season_shop.back", lang), "m", season_id))
    return _Offer(text, InlineKeyboardMarkup([buttons]), _resolved(row))


def _language(context: ContextTypes.DEFAULT_TYPE) -> str:
    with session_scope(context.bot_data["session_factory"]) as session:
        return settings.get_language(session)


async def _stale(query: CallbackQuery, context: ContextTypes.DEFAULT_TYPE, season_id: int) -> None:
    logger.warning("refused a /seasonshop tap: season {tapped} isn't open", tapped=season_id)
    await query.answer(i18n.t("season_shop.closed", _language(context)), show_alert=True)


async def _refuse(
    query: CallbackQuery,
    context: ContextTypes.DEFAULT_TYPE,
    refusal: ShopRefusal,
    **numbers: int,
) -> None:
    lang = _language(context)
    await query.answer(
        i18n.t(f"season_shop.refused.{refusal.value}", lang, **numbers), show_alert=True
    )


async def _send_preview(
    context: ContextTypes.DEFAULT_TYPE, user: User, offer: _Offer, lang: str
) -> None:
    """Into the shopper's DM (its chat id is their user id), drawn on them."""
    if offer.resolved is not None:
        await item_preview.send(context, user.id, offer.resolved, lang, user)
        return
    picture = item_preview.Picture(CLUE_ICON_KEY, clue_token_icon.icon)
    await item_preview.send_image(context, user.id, picture, lang)


async def _on_offer(
    query: CallbackQuery, context: ContextTypes.DEFAULT_TYPE, season_id: int, index: int
) -> None:
    lang = _language(context)
    with session_scope(context.bot_data["session_factory"]) as session:
        offer = _offer(session, season_id, query.from_user.id, index, lang)
    if isinstance(offer, ShopRefusal):
        logger.warning(
            "refused a /seasonshop tap: {refusal}",
            refusal=offer.value,
            index=index,
            season_id=season_id,
        )
        await _refuse(query, context, offer)
        return
    if offer is None:
        await _stale(query, context, season_id)
        return
    logger.info("opened offer {index} of the season shop", index=index, season_id=season_id)
    await query.answer()
    await _send_preview(context, query.from_user, offer, lang)
    await context.bot.send_message(
        chat_id=query.from_user.id, text=offer.text, reply_markup=offer.keyboard
    )


async def _on_menu(
    query: CallbackQuery, context: ContextTypes.DEFAULT_TYPE, season_id: int, _index: int
) -> None:
    with session_scope(context.bot_data["session_factory"]) as session:
        text, keyboard = view(session, query.from_user.id, settings.get_language(session))
    logger.info("went back to the season shop menu", season_id=season_id)
    await query.answer()
    await query.edit_message_text(text, reply_markup=keyboard)


def _purchase(
    session: Session, user: User, season_id: int, index: int, lang: str
) -> tuple[str, InlineKeyboardMarkup] | None:
    """Buys the offer; returns the result page, or None when the season isn't the open one."""
    open_run = _open(session, season_id)
    if open_run is None:
        return None
    season, run = open_run
    player = players.get_or_create_player(session, user.id, username=user.username)
    row = season_shop.buy(session, season, run, player, index)
    text = i18n.t(
        "season_shop.bought",
        lang,
        name=_name(row, lang),
        balance=wallet.balance(session, user.id, season_id=season.id),
    )
    if row.item_id is not None:
        text += "\n" + i18n.t("season_shop.equip_hint", lang)
    back = _button(i18n.t("season_shop.back", lang), "m", season_id)
    return text, InlineKeyboardMarkup([[back]])


def _refusal_numbers(
    context: ContextTypes.DEFAULT_TYPE, user_id: int, season_id: int, index: int
) -> dict[str, int]:
    """The price and the 🪙 balance an INSUFFICIENT alert quotes; 0 for what can't be read."""
    price = balance = 0
    with session_scope(context.bot_data["session_factory"]) as session:
        open_run = _open(session, season_id)
        if open_run is not None:
            season, run = open_run
            balance = wallet.balance(session, user_id, season_id=season.id)
            if 0 <= index < len(run.shop):
                price = run.shop[index].price
    return {"price": price, "balance": balance}


async def _on_buy(
    query: CallbackQuery, context: ContextTypes.DEFAULT_TYPE, season_id: int, index: int
) -> None:
    lang = _language(context)
    try:
        with session_scope(context.bot_data["session_factory"]) as session:
            done = _purchase(session, query.from_user, season_id, index, lang)
    except ShopRefusedError as error:
        numbers = _refusal_numbers(context, query.from_user.id, season_id, index)
        await _refuse(query, context, error.refusal, **numbers)
        return
    except Exception:
        logger.opt(exception=True).error(
            "buying offer {index} of the season shop failed", index=index, season_id=season_id
        )
        await query.answer(i18n.t("season_shop.failed", lang), show_alert=True)
        return
    if done is None:
        await _stale(query, context, season_id)
        return
    await query.answer()
    await query.edit_message_text(done[0], reply_markup=done[1])


_TAPS = MappingProxyType({"i": _on_offer, "b": _on_buy, "m": _on_menu})
_INDEXED = frozenset({"i", "b"})  # taps that name an offer as well as a season


def _numbers(args: list[str]) -> list[int] | None:
    if not all(arg.isdecimal() and int(arg) <= MAX_ID for arg in args):
        return None
    return [int(arg) for arg in args]


async def season_shop_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    if query is None or query.data is None or query.from_user is None:
        return
    action, *args = query.data.removeprefix(PREFIX).split(":")
    tap, numbers = _TAPS.get(action), _numbers(args)
    if tap is None or numbers is None or len(numbers) != (2 if action in _INDEXED else 1):
        logger.warning("ignored a malformed /seasonshop tap {data!r}", data=query.data)
        await query.answer()
        return
    refusal = await outsider_refusal(context, query.from_user.id, "/seasonshop tap")
    if refusal is not None:
        await query.answer(refusal, show_alert=True)
        return
    await tap(query, context, numbers[0], numbers[-1] if len(numbers) > 1 else 0)
