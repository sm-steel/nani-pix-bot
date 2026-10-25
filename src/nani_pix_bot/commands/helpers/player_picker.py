"""The player picker every command that targets another player shows when no
@username was given: one button per candidate, PAGE_SIZE to a page, ◀ / ▶ only
when there is a page that way, and ✖ cancel. Each command supplies its own
candidates (see `services/players.py::by_activity` for the usual order), what
a tap does, and its own callback prefix; nothing here knows what the pick is for.

Callback data is `<prefix><opener_id>[:<state>...]:<tail>`. The state parts are
whatever the command needs to carry (an amount, a game id), and the tail is
`p:<page>`, `t:<target_id>`, `x` (cancel), or the command's own parts. Only the
opener's taps count: `read_tap` refuses anyone else's with an alert."""

from collections.abc import Callable, Sequence
from dataclasses import dataclass

from loguru import logger
from sqlalchemy.orm import Session
from telegram import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.error import BadRequest
from telegram.ext import ContextTypes

from nani_pix_bot.commands.helpers.ids import MAX_ID
from nani_pix_bot.commands.helpers.paging import clamp_page
from nani_pix_bot.db import session_scope
from nani_pix_bot.services import i18n, players, settings

PAGE_SIZE = 8

View = tuple[str, InlineKeyboardMarkup | None]


@dataclass(frozen=True)
class PickEntry:
    player_id: int
    label: str  # the button text: the plain display name


@dataclass(frozen=True)
class PickerData:
    """The callback data a command gives its picker's buttons."""

    pick: Callable[[PickEntry], str]
    page: Callable[[int], str]
    cancel: str


def clamp(entries: Sequence[PickEntry], page: int) -> int:
    """`page` within the pages `entries` fill; an out-of-range page is the last one."""
    return clamp_page(page, total=len(entries), size=PAGE_SIZE)


def _nav_row(count: int, page: int, data: PickerData, lang: str) -> list[InlineKeyboardButton]:
    row = []
    if page > 0:
        row.append(
            InlineKeyboardButton(
                i18n.t("player_picker.prev", lang), callback_data=data.page(page - 1)
            )
        )
    row.append(
        InlineKeyboardButton(i18n.t("player_picker.cancel", lang), callback_data=data.cancel)
    )
    if (page + 1) * PAGE_SIZE < count:
        row.append(
            InlineKeyboardButton(
                i18n.t("player_picker.next", lang), callback_data=data.page(page + 1)
            )
        )
    return row


def picker_keyboard(
    entries: Sequence[PickEntry], page: int, data: PickerData, lang: str
) -> InlineKeyboardMarkup:
    """One row per candidate on the (clamped) page, then the ◀ ✖ ▶ row."""
    page = clamp(entries, page)
    shown = entries[page * PAGE_SIZE : (page + 1) * PAGE_SIZE]
    rows = [[InlineKeyboardButton(entry.label, callback_data=data.pick(entry))] for entry in shown]
    rows.append(_nav_row(len(entries), page, data, lang))
    return InlineKeyboardMarkup(rows)


# --- callback data ---


@dataclass(frozen=True)
class Picker:
    """One picker's callback head: who opened it, and the state it carries."""

    prefix: str
    opener_id: int
    state: tuple[str, ...] = ()

    def _head(self) -> str:
        return ":".join([f"{self.prefix}{self.opener_id}", *self.state])

    def page(self, page: int) -> str:
        return f"{self._head()}:p:{page}"

    def target(self, target_id: int) -> str:
        return f"{self._head()}:t:{target_id}"

    def cancel(self) -> str:
        return f"{self._head()}:x"

    def data(self) -> PickerData:
        return PickerData(
            pick=lambda entry: self.target(entry.player_id), page=self.page, cancel=self.cancel()
        )


@dataclass(frozen=True)
class Tap:
    picker: Picker
    action: str  # "p" (page), "t" (target), "x" (cancel), or "" (the command's own tail)
    number: int = 0  # the page or the target
    rest: tuple[str, ...] = ()  # the command's own tail, when action is ""


def number(text: str) -> int | None:
    """A callback-data id or page: decimal and within the DB's BigInteger."""
    return int(text) if text.isdecimal() and int(text) <= MAX_ID else None


def _tail_tap(picker: Picker, tail: list[str]) -> Tap | None:
    if tail == ["x"]:
        return Tap(picker, "x")
    if len(tail) == 2 and tail[0] in ("p", "t"):
        value = number(tail[1])
        return None if value is None else Tap(picker, tail[0], value)
    return Tap(picker, "", rest=tuple(tail))


def parse_tap(data: str, prefix: str, state_parts: int = 0) -> Tap | None:
    """The tap `data` stands for, or None when it isn't one of `prefix`'s."""
    opener, *parts = data.removeprefix(prefix).split(":")
    opener_id = number(opener)
    if opener_id is None or len(parts) <= state_parts:
        return None
    return _tail_tap(Picker(prefix, opener_id, tuple(parts[:state_parts])), parts[state_parts:])


# --- the taps every picker handles the same way ---


@dataclass(frozen=True)
class PickerSpec:
    """What a command's picker taps share: its prefix, how many state parts its
    callback data carries, its name in the log, and its two reply keys."""

    prefix: str
    command: str  # e.g. "/tip", for the log
    state_parts: int = 0
    not_yours_key: str = "player_picker.not_yours"
    cancelled_key: str = "player_picker.cancelled"


def language(context: ContextTypes.DEFAULT_TYPE) -> str:
    with session_scope(context.bot_data["session_factory"]) as session:
        return settings.get_language(session)


async def read_tap(
    update: Update, context: ContextTypes.DEFAULT_TYPE, spec: PickerSpec
) -> tuple[CallbackQuery, Tap] | None:
    """The query and its tap, when the opener made a well-formed one; anything
    else is answered (someone else's tap with an alert) and gives None."""
    query = update.callback_query
    if query is None or query.data is None or query.from_user is None:
        return None
    tap = parse_tap(query.data, spec.prefix, spec.state_parts)
    if tap is None:
        await malformed(query, spec)
        return None
    if query.from_user.id != tap.picker.opener_id:
        logger.warning(
            "refused a {command} tap on someone else's picker",
            command=spec.command,
            opener_id=tap.picker.opener_id,
        )
        await query.answer(i18n.t(spec.not_yours_key, language(context)), show_alert=True)
        return None
    return query, tap


async def malformed(query: CallbackQuery, spec: PickerSpec) -> None:
    logger.warning(
        "ignored a malformed {command} tap {data!r}", command=spec.command, data=query.data
    )
    await query.answer()


async def refuse_stale(query: CallbackQuery, context: ContextTypes.DEFAULT_TYPE) -> None:
    """A tap on a picker that already acted, or that is out of date (the
    caller logs why)."""
    await query.answer(i18n.t("player_picker.stale", language(context)), show_alert=True)


async def edit(query: CallbackQuery, view: View) -> None:
    """Edit the picker; a double tap (not modified) or a vanished message isn't an error."""
    text, keyboard = view
    try:
        await query.edit_message_text(text, reply_markup=keyboard)
    except BadRequest as error:
        logger.warning("couldn't edit the player picker: {error}", error=str(error))


async def cancel(
    query: CallbackQuery, context: ContextTypes.DEFAULT_TYPE, spec: PickerSpec
) -> None:
    logger.info("cancelled the {command} picker", command=spec.command)
    await query.answer()
    await edit(query, (i18n.t(spec.cancelled_key, language(context)), None))


async def turn_page(query: CallbackQuery, spec: PickerSpec, page: int, view: View) -> None:
    logger.info("turned the {command} picker to page {page}", command=spec.command, page=page)
    await query.answer()
    await edit(query, view)


def entries(session: Session, player_ids: Sequence[int]) -> list[PickEntry]:
    """One entry per id, in that order, labelled unmasked: whoever is choosing
    must tell faceless players apart."""
    return [PickEntry(pid, players.display_name(session, pid, unmasked=True)) for pid in player_ids]
