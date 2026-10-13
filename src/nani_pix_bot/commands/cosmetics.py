"""/cosmetics — browse your season cosmetics per slot, preview one, equip it (DM only).

Buttons carry `OwnedItem.id`, not the item id: the latter may not fit Telegram's 64-byte
callback data. A row id that isn't the tapper's is refused (`cosmetics.not_yours`)."""

from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from types import MappingProxyType

from loguru import logger
from sqlalchemy.orm import Session
from telegram import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import ContextTypes

from nani_pix_bot.commands.achievements.common import MAX_ID, outsider_refusal
from nani_pix_bot.commands.helpers import item_preview
from nani_pix_bot.commands.helpers.scoping import is_private_chat
from nani_pix_bot.db import session_scope
from nani_pix_bot.models.enums import CosmeticSlot
from nani_pix_bot.seasons.registry import ResolvedItem
from nani_pix_bot.services import i18n, settings
from nani_pix_bot.services.cosmetics import inventory

PREFIX = "cos:"
_MENU = f"{PREFIX}m"
_EQUIPPED_MARK = "✅ "
# Titles are owned like any cosmetic but shown through /title (Player.title_key), so
# /cosmetics previews them without an Equip button.
_PICKED_IN_TITLE = CosmeticSlot.TITLE

View = tuple[str, InlineKeyboardMarkup | None]


def _slot_name(slot: CosmeticSlot, lang: str) -> str:
    return i18n.t(f"cosmetics.slot.{slot.value}", lang)


def _button(text: str, data: str) -> list[InlineKeyboardButton]:
    return [InlineKeyboardButton(text, callback_data=data)]


def _equipped_id(session: Session, player_id: int, slot: CosmeticSlot) -> str | None:
    on = inventory.equipped(session, player_id, slot)
    return None if on is None else on.item_id


def _menu_line(session: Session, player_id: int, slot: CosmeticSlot, lang: str) -> str:
    name, count = _slot_name(slot, lang), len(inventory.owned(session, player_id, slot))
    if slot is _PICKED_IN_TITLE:
        return i18n.t("cosmetics.menu_line_title", lang, slot=name, count=count)
    on = inventory.equipped(session, player_id, slot)
    return i18n.t(
        "cosmetics.menu_line",
        lang,
        slot=name,
        count=count,
        equipped=on.item.name(lang) if on else i18n.t("cosmetics.nothing_equipped", lang),
    )


def _menu(session: Session, player_id: int, lang: str) -> View:
    """Every slot with its owned count and what's equipped; a button per slot you own
    something in. Nothing owned → the empty text, no buttons."""
    owned_slots = {r.item.slot for r in inventory.owned(session, player_id)}
    if not owned_slots:
        return i18n.t("cosmetics.empty", lang), None
    lines = "\n".join(_menu_line(session, player_id, slot, lang) for slot in CosmeticSlot)
    rows = [
        _button(_slot_name(slot, lang), f"{PREFIX}s:{slot.value}")
        for slot in CosmeticSlot
        if slot in owned_slots
    ]
    return i18n.t("cosmetics.menu", lang, slots=lines), InlineKeyboardMarkup(rows)


def _slot_view(
    session: Session, player_id: int, slot: CosmeticSlot, lang: str, notice: str | None = None
) -> View:
    on = _equipped_id(session, player_id, slot)
    rows = [
        _button(
            f"{_EQUIPPED_MARK if r.item_id == on else ''}{r.item.name(lang)}",
            f"{PREFIX}i:{owned_id}",
        )
        for owned_id, r in inventory.owned_rows(session, player_id, slot)
    ]
    rows.append(_button(i18n.t("cosmetics.back", lang), _MENU))
    text = i18n.t("cosmetics.slot_view", lang, slot=_slot_name(slot, lang))
    return (text if notice is None else f"{notice}\n\n{text}"), InlineKeyboardMarkup(rows)


def _item_view(owned_id: int, resolved: ResolvedItem, equipped: bool, lang: str) -> View:
    slot = resolved.item.slot
    key = "cosmetics.item_equipped" if equipped else "cosmetics.item"
    text = i18n.t(key, lang, item=resolved.item.name(lang), slot=_slot_name(slot, lang))
    back = _button(i18n.t("cosmetics.back", lang), f"{PREFIX}s:{slot.value}")
    if slot is _PICKED_IN_TITLE:
        return f"{text}\n{i18n.t('cosmetics.title_hint', lang)}", InlineKeyboardMarkup([back])
    action = (
        _button(i18n.t("cosmetics.unequip", lang), f"{PREFIX}u:{slot.value}")
        if equipped
        else _button(i18n.t("cosmetics.equip", lang), f"{PREFIX}e:{owned_id}")
    )
    return text, InlineKeyboardMarkup([action, back])


async def cosmetics_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    message, user = update.message, update.effective_user
    if not is_private_chat(update) or message is None or user is None:
        return
    refusal = await outsider_refusal(context, user.id, "/cosmetics")
    if refusal is not None:
        await message.reply_text(refusal)
        return
    with session_scope(context.bot_data["session_factory"]) as session:
        lang = settings.get_language(session)
        text, markup = _menu(session, user.id, lang)
    logger.info("opened /cosmetics{empty}", empty="" if markup else " (nothing owned)")
    if markup is None:
        await message.reply_text(text)
    else:
        await message.reply_text(text, reply_markup=markup)


async def _edit(query: CallbackQuery, view: View) -> None:
    text, markup = view
    if markup is None:
        await query.edit_message_text(text)
    else:
        await query.edit_message_text(text, reply_markup=markup)


async def _refuse(query: CallbackQuery, context: ContextTypes.DEFAULT_TYPE, data: str) -> None:
    with session_scope(context.bot_data["session_factory"]) as session:
        lang = settings.get_language(session)
    logger.warning("refused a /cosmetics tap on an item that isn't theirs: {data!r}", data=data)
    await query.answer(i18n.t("cosmetics.not_yours", lang), show_alert=True)


async def _ignore(query: CallbackQuery, data: str) -> None:
    logger.warning("ignored a malformed /cosmetics tap {data!r}", data=data)
    await query.answer()


def _owned_id(arg: str) -> int | None:
    return int(arg) if arg.isdecimal() and int(arg) <= MAX_ID else None


def _slot(arg: str) -> CosmeticSlot | None:
    try:
        return CosmeticSlot(arg)
    except ValueError:
        return None


# --- taps ---


async def _on_menu(query: CallbackQuery, context: ContextTypes.DEFAULT_TYPE, _arg: str) -> None:
    with session_scope(context.bot_data["session_factory"]) as session:
        view = _menu(session, query.from_user.id, settings.get_language(session))
    logger.info("went back to the /cosmetics menu")
    await query.answer()
    await _edit(query, view)


async def _on_slot(query: CallbackQuery, context: ContextTypes.DEFAULT_TYPE, arg: str) -> None:
    slot = _slot(arg)
    if slot is None:
        await _ignore(query, str(query.data))
        return
    with session_scope(context.bot_data["session_factory"]) as session:
        view = _slot_view(session, query.from_user.id, slot, settings.get_language(session))
    logger.info("opened the /cosmetics {slot} slot", slot=slot.value)
    await query.answer()
    await _edit(query, view)


def _equip(session: Session, user_id: int, owned_id: int | None, lang: str) -> View | None:
    """Equips the tapped row's item; None when that row isn't the tapper's."""
    resolved = None if owned_id is None else inventory.owned_by(session, user_id, owned_id)
    if resolved is None:
        return None
    slot = resolved.item.slot
    if slot is _PICKED_IN_TITLE:
        logger.warning(
            "tapped Equip on title {item_id}; titles go through /title", item_id=resolved.item_id
        )
        return _slot_view(session, user_id, slot, lang, i18n.t("cosmetics.title_hint", lang))
    inventory.equip(session, user_id, resolved.item_id)
    notice = i18n.t("cosmetics.equipped", lang, item=resolved.item.name(lang))
    return _slot_view(session, user_id, resolved.item.slot, lang, notice)


async def _on_equip(query: CallbackQuery, context: ContextTypes.DEFAULT_TYPE, arg: str) -> None:
    with session_scope(context.bot_data["session_factory"]) as session:
        lang = settings.get_language(session)
        view = _equip(session, query.from_user.id, _owned_id(arg), lang)
    if view is None:
        await _refuse(query, context, str(query.data))
        return
    await query.answer()
    await _edit(query, view)


async def _on_unequip(query: CallbackQuery, context: ContextTypes.DEFAULT_TYPE, arg: str) -> None:
    slot = _slot(arg)
    if slot is None:
        await _ignore(query, str(query.data))
        return
    user_id = query.from_user.id
    with session_scope(context.bot_data["session_factory"]) as session:
        lang = settings.get_language(session)
        inventory.unequip(session, user_id, slot)
        view = _slot_view(session, user_id, slot, lang, i18n.t("cosmetics.unequipped", lang))
    await query.answer()
    await _edit(query, view)


@dataclass(frozen=True)
class _ItemTap:
    owned_id: int
    resolved: ResolvedItem
    equipped: bool


def _load_item(session: Session, user_id: int, owned_id: int | None) -> _ItemTap | None:
    """The tapped row's item with its state; None when that row isn't the tapper's."""
    resolved = None if owned_id is None else inventory.owned_by(session, user_id, owned_id)
    if owned_id is None or resolved is None:
        return None
    return _ItemTap(
        owned_id,
        resolved,
        equipped=_equipped_id(session, user_id, resolved.item.slot) == resolved.item_id,
    )


async def _on_item(query: CallbackQuery, context: ContextTypes.DEFAULT_TYPE, arg: str) -> None:
    user_id = query.from_user.id
    with session_scope(context.bot_data["session_factory"]) as session:
        lang = settings.get_language(session)
        tap = _load_item(session, user_id, _owned_id(arg))
    if tap is None:
        await _refuse(query, context, str(query.data))
        return
    await query.answer()
    await item_preview.send(context, user_id, tap.resolved, lang, user_id)
    text, markup = _item_view(tap.owned_id, tap.resolved, tap.equipped, lang)
    await context.bot.send_message(chat_id=user_id, text=text, reply_markup=markup)


_TAPS: Mapping[str, Callable[[CallbackQuery, ContextTypes.DEFAULT_TYPE, str], Awaitable[None]]] = (
    MappingProxyType(
        {"m": _on_menu, "s": _on_slot, "i": _on_item, "e": _on_equip, "u": _on_unequip}
    )
)


async def cosmetics_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    if query is None or query.data is None or query.from_user is None:
        return
    action, _, arg = query.data.removeprefix(PREFIX).partition(":")
    tap = _TAPS.get(action)
    if tap is None:
        await _ignore(query, query.data)
        return
    await tap(query, context, arg)
