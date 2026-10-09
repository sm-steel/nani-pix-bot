"""/history's callback data. Everything after the prefix is client-controlled,
so it is parsed defensively (field count, filter and tab values, int bounds):

- hist:l:<filter>:<page>                            the list
- hist:g:<game>:<filter>:<page>:<tab>:<guess_page>  one game; filter and page
                                                     are the list to go back to

A game button from before the tabs (hist:g:<game>:<filter>:<page>:<guess_page>)
opens the Record tab.
"""

from dataclasses import dataclass
from enum import StrEnum

from nani_pix_bot.commands.achievements.common import MAX_ID
from nani_pix_bot.services.game.history import Involvement as Filter

PREFIX = "hist:"
_LEGACY_GAME_FIELDS = 4
_GAME_FIELDS = 5


class Tab(StrEnum):
    RECORD = "r"
    GUESSES = "g"


@dataclass(frozen=True)
class ListRequest:
    filt: Filter = Filter.ALL
    page: int = 0


@dataclass(frozen=True)
class GameRequest:
    game_id: int
    back: ListRequest
    tab: Tab = Tab.RECORD
    guess_page: int = 0


def list_data(request: ListRequest) -> str:
    return f"{PREFIX}l:{request.filt.value}:{request.page}"


def game_data(request: GameRequest) -> str:
    back = request.back
    where = f"{request.game_id}:{back.filt.value}:{back.page}"
    return f"{PREFIX}g:{where}:{request.tab.value}:{request.guess_page}"


def _int(raw: str) -> int | None:
    return int(raw) if raw.isdecimal() and int(raw) <= MAX_ID else None


def _list(filt: str, page: str) -> ListRequest | None:
    number = _int(page)
    if filt not in {f.value for f in Filter} or number is None:
        return None
    return ListRequest(Filter(filt), number)


def _game(fields: list[str]) -> GameRequest | None:
    if len(fields) == _LEGACY_GAME_FIELDS:
        fields = [*fields[:3], Tab.RECORD.value, "0"]
    if len(fields) != _GAME_FIELDS or fields[3] not in {tab.value for tab in Tab}:
        return None
    game_id, guess_page = _int(fields[0]), _int(fields[4])
    back = _list(fields[1], fields[2])
    if game_id is None or guess_page is None or back is None:
        return None
    return GameRequest(game_id, back, Tab(fields[3]), guess_page)


def parse(data: str) -> ListRequest | GameRequest | None:
    if not data.startswith(PREFIX):
        return None
    action, *fields = data.removeprefix(PREFIX).split(":")
    if action == "l" and len(fields) == 2:
        return _list(*fields)
    if action == "g":
        return _game(fields)
    return None
