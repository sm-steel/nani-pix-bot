"""The /grantitem menu: `/grantitem <item id>` with no @username lists every
known player, most active first, and a pick grants them the item exactly as
`/grantitem @user <item id>` does. Callback data carries the item as a digest:

- `gi:<admin>:<digest>:t:<player>`: grant the item to that player.
- `gi:<admin>:<digest>:p:<page>`: a page of the menu.
- `gi:<admin>:<digest>:x`: cancel.

Item ids run to ~40 bytes, too long beside two Telegram ids within the 64-byte
cap, so <digest> is the first DIGEST_LENGTH hex characters of the id's sha1,
looked up again among every registered item (a test keeps them unique). A
pick re-checks the admin's rights, and each menu message acts once
(services/used_menus.py)."""

import hashlib

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
from nani_pix_bot.services import i18n, players, settings, used_menus

PREFIX = "gi:"
SPEC = PickerSpec(PREFIX, "/grantitem", state_parts=1)
DIGEST_LENGTH = 8


def item_digest(item_id: str) -> str:
    """The short, fixed-length stand-in for `item_id` in callback data."""
    return hashlib.sha1(item_id.encode(), usedforsecurity=False).hexdigest()[:DIGEST_LENGTH]


def item_by_digest(digest: str) -> ResolvedItem | None:
    """The registered item whose id has this digest; None for an unknown one."""
    return next(
        (item for item_id, item in registry.all_items().items() if item_digest(item_id) == digest),
        None,
    )


def picker(admin_id: int, item_id: str) -> Picker:
    return Picker(PREFIX, admin_id, (item_digest(item_id),))


def _candidates(session: Session, bot_id: int) -> list[int]:
    """Every known player but Nani Pix herself, most active first."""
    known = [pid for pid in players.known_ids(session) if pid != bot_id]
    return players.by_activity(session, known, None)


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
    logger.info(
        "opened the /grantitem picker",
        item_id=resolved.item_id,
        has_targets=bool(candidates),
    )
    if not candidates:
        return i18n.t("grant_item.no_one", settings.get_language(session)), None
    return _view(session, picker(admin_id, resolved.item_id), resolved, candidates, 0)


def _grant(query: CallbackQuery, context: ContextTypes.DEFAULT_TYPE, tap: Tap) -> str | None:
    """Grant the item to the tapped player: the reply, or None for a stale or
    used menu."""
    resolved, target_id = item_by_digest(tap.picker.state[0]), tap.number
    with session_scope(context.bot_data["session_factory"]) as session:
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
    resolved = item_by_digest(tap.picker.state[0])
    if resolved is None:
        logger.warning("refused a stale /grantitem page")
        await player_picker.refuse_stale(query, context)
        return
    with session_scope(context.bot_data["session_factory"]) as session:
        candidates = _candidates(session, context.bot.id)
        view = _view(session, tap.picker, resolved, candidates, tap.number)
    await player_picker.turn_page(query, SPEC, tap.number, view)


async def _cancel(query: CallbackQuery, context: ContextTypes.DEFAULT_TYPE, _tap: Tap) -> None:
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
