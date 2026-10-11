"""Callback data of every /profile card button. <owner> is whoever sent /profile;
only they may tap. Everything after the prefix is client-controlled, so it is
parsed defensively.

- prof:o:<owner>:<target>:<via>        show <target>'s card (browse.py)
- prof:l:<owner>:<page>                the player list (browse.py)
- prof:e:<owner>:<kind>:<page>         the "what my card shows" editor (edit.py)
- prof:t:<owner>:<kind>:<id>:<page>    show/hide one stamp or achievement (edit.py)

<kind> is s (season stamps; <id> is the season_schedule row) or a
(achievements; <id> is the achievement grant)."""

from nani_pix_bot.commands.achievements.common import MAX_ID
from nani_pix_bot.models.enums import ShowcaseKind

PREFIX = "prof:"
# How a card was reached, as it reads in the log.
VIAS = {"p": "◀", "n": "▶", "l": "the player list", "m": "my card"}
KINDS = {"s": ShowcaseKind.STAMP, "a": ShowcaseKind.ACHIEVEMENT}
KIND_CODES = {kind: code for code, kind in KINDS.items()}
# action -> its fields, and which of them are ints
_SHAPES: dict[str, tuple[bool, ...]] = {
    "o": (True, True, False),
    "l": (True, True),
    "e": (True, False, True),
    "t": (True, False, True, True),
}


def card_data(owner: int, target: int, via: str) -> str:
    return f"{PREFIX}o:{owner}:{target}:{via}"


def list_data(owner: int, page: int) -> str:
    return f"{PREFIX}l:{owner}:{page}"


def edit_data(owner: int, kind: ShowcaseKind, page: int) -> str:
    return f"{PREFIX}e:{owner}:{KIND_CODES[kind]}:{page}"


def toggle_data(owner: int, kind: ShowcaseKind, ref_id: int, page: int) -> str:
    return f"{PREFIX}t:{owner}:{KIND_CODES[kind]}:{ref_id}:{page}"


def _valid_text(action: str, fields: list[str]) -> bool:
    if action == "o":
        return fields[2] in VIAS
    if action in ("e", "t"):
        return fields[1] in KINDS
    return True


def parse(data: str) -> tuple[str, list[int | str]] | None:
    action, *fields = data.removeprefix(PREFIX).split(":")
    shape = _SHAPES.get(action)
    if shape is None or len(fields) != len(shape):
        return None
    pairs = list(zip(fields, shape, strict=True))
    if any(is_int and not (raw.isdecimal() and int(raw) <= MAX_ID) for raw, is_int in pairs):
        return None
    if not _valid_text(action, fields):
        return None
    return action, [int(raw) if is_int else raw for raw, is_int in pairs]
