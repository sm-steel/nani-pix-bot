"""The /grantitem menu: `/grantitem <item id>` with no @username lists every
known player, most active first, and a pick grants them the item exactly as
`/grantitem @user <item id>` does. Callback data carries the item:

- `gi:<admin>:<item>:t:<player>`: grant the item to that player.
- `gi:<admin>:<item>:p:<page>`: a page of the menu.
- `gi:<admin>:<item>:x`: cancel.

<item> is the item id itself when every button's data stays within
Telegram's 64 bytes, else `#<row>`, a services/pending_picks.py row holding
it (item ids never contain `#`); the row goes once the menu grants or is
cancelled. A pick re-checks the admin's rights, and each menu message acts
once (services/used_menus.py)."""

from collections.abc import Sequence

from loguru import logger
from sqlalchemy.orm import Session
from telegram import CallbackQuery, Update
from telegram.ext import ContextTypes

from nani_pix_bot.commands import grant_item_apply
from nani_pix_bot.commands.helpers import player_picker
from nani_pix_bot.commands.helpers.player_picker import Picker, PickerSpec, Tap, View
from nani_pix_bot.db import session_scope
from nani_pix_bot.seasons import registry
from nani_pix_bot.seasons.registry import ResolvedItem
from nani_pix_bot.services import i18n, pending_picks, players, settings, used_menus

PREFIX = "gi:"
SPEC = PickerSpec(PREFIX, "/grantitem", state_parts=1)
CALLBACK_DATA_LIMIT = 64  # bytes, Telegram's cap
_KEPT = "#"


def _candidates(session: Session, bot_id: int) -> list[int]:
    """Every known player but Nani Pix herself, most active first."""
    known = [pid for pid in players.known_ids(session) if pid != bot_id]
    return players.by_activity(session, known, None)


def _item_state(session: Session, admin_id: int, item_id: str, candidates: Sequence[int]) -> str:
    """The item id when the longest button's data fits, else a kept row's `#<id>`."""
    longest = Picker(PREFIX, admin_id, (item_id,)).target(max(candidates))
    if len(longest.encode()) <= CALLBACK_DATA_LIMIT:
        return item_id
    return f"{_KEPT}{pending_picks.keep(session, item_id)}"


def _view(
    session: Session, at: Picker, resolved: ResolvedItem, candidates: list[int], page: int
) -> View:
    lang = settings.get_language(session)
    text = i18n.t(
        "grant_item.pick_player", lang, item=resolved.item.name(lang), item_id=resolved.item_id
    )
    found = player_picker.entries(session, candidates)
    return text, player_picker.picker_keyboard(found, page, at.data(), lang)


def open_view(session: Session, admin_id: int, resolved: ResolvedItem, bot_id: int) -> View:
    """The menu for granting `resolved`, or the "no one" reply."""
    candidates = _candidates(session, bot_id)
    if not candidates:
        logger.info("opened the /grantitem picker", item_id=resolved.item_id, has_targets=False)
        return i18n.t("grant_item.no_one", settings.get_language(session)), None
    state = _item_state(session, admin_id, resolved.item_id, candidates)
    logger.info(
        "opened the /grantitem picker",
        item_id=resolved.item_id,
        has_targets=True,
        kept=state.startswith(_KEPT),
    )
    return _view(session, Picker(PREFIX, admin_id, (state,)), resolved, candidates, 0)


def _resolve(session: Session, state: str) -> ResolvedItem | None:
    """The item a menu's state names; None once its kept row is gone."""
    item_id: str | None = state
    if state.startswith(_KEPT):
        row_id = player_picker.number(state.removeprefix(_KEPT))
        item_id = None if row_id is None else pending_picks.item_id(session, row_id)
    return None if item_id is None else registry.item(item_id)


def _forget(session: Session, state: str) -> None:
    row_id = player_picker.number(state.removeprefix(_KEPT))
    if state.startswith(_KEPT) and row_id is not None:
        pending_picks.forget(session, row_id)


def _grant(query: CallbackQuery, context: ContextTypes.DEFAULT_TYPE, tap: Tap) -> str | None:
    """Grant the item to the tapped player: the reply, or None for a stale or
    used menu."""
    state, target_id = tap.picker.state[0], tap.number
    with session_scope(context.bot_data["session_factory"]) as session:
        resolved = _resolve(session, state)
        if resolved is None or target_id not in _candidates(session, context.bot.id):
            logger.warning("refused a stale /grantitem pick", target_id=target_id)
            return None
        message = query.message
        if message is not None and not used_menus.claim(
            session, message.chat.id, message.message_id
        ):
            logger.warning("refused a second tap on a used /grantitem menu")
            return None
        logger.info(
            "picked {target} to grant {item_id}",
            target=players.describe_player_id(session, target_id),
            target_id=target_id,
            item_id=resolved.item_id,
        )
        _forget(session, state)
        return grant_item_apply.give(session, target_id, resolved, settings.get_language(session))


async def _pick(query: CallbackQuery, context: ContextTypes.DEFAULT_TYPE, tap: Tap) -> None:
    if not await player_picker.still_admin(query, context, SPEC):
        return
    reply = _grant(query, context, tap)
    if reply is None:
        await player_picker.refuse_stale(query, context)
        return
    await query.answer()
    await player_picker.edit(query, (reply, None))


async def _page(query: CallbackQuery, context: ContextTypes.DEFAULT_TYPE, tap: Tap) -> None:
    with session_scope(context.bot_data["session_factory"]) as session:
        resolved = _resolve(session, tap.picker.state[0])
        view = (
            None
            if resolved is None
            else _view(
                session, tap.picker, resolved, _candidates(session, context.bot.id), tap.number
            )
        )
    if view is None:
        logger.warning("refused a stale /grantitem page")
        await player_picker.refuse_stale(query, context)
        return
    await player_picker.turn_page(query, SPEC, tap.number, view)


async def _cancel(query: CallbackQuery, context: ContextTypes.DEFAULT_TYPE, tap: Tap) -> None:
    with session_scope(context.bot_data["session_factory"]) as session:
        _forget(session, tap.picker.state[0])
    await player_picker.cancel(query, context, SPEC)


async def grant_item_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    read = await player_picker.read_tap(update, context, SPEC)
    if read is None:
        return
    query, tap = read
    handlers = {"x": _cancel, "p": _page, "t": _pick}
    handler = handlers.get(tap.action)
    if handler is None:
        await player_picker.malformed(query, SPEC)
        return
    await handler(query, context, tap)
