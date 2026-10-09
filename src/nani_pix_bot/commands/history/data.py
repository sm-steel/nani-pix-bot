"""/history's callback data. Everything after the prefix is client-controlled,
so it is parsed defensively (field count, filter value, int bounds):

- hist:l:<filter>:<page>                      the list
- hist:g:<game>:<filter>:<page>:<guess_page>  one game; filter and page are
                                               the list to go back to
"""

from dataclasses import dataclass

from nani_pix_bot.commands.achievements.common import MAX_ID
from nani_pix_bot.services.game.history import Involvement as Filter

PREFIX = "hist:"


@dataclass(frozen=True)
class ListRequest:
    filt: Filter = Filter.ALL
    page: int = 0


@dataclass(frozen=True)
class GameRequest:
    game_id: int
    back: ListRequest
    guess_page: int = 0


def list_data(request: ListRequest) -> str:
    return f"{PREFIX}l:{request.filt.value}:{request.page}"


def game_data(request: GameRequest) -> str:
    back = request.back
    return f"{PREFIX}g:{request.game_id}:{back.filt.value}:{back.page}:{request.guess_page}"


def _int(raw: str) -> int | None:
    return int(raw) if raw.isdecimal() and int(raw) <= MAX_ID else None


def _list(filt: str, page: str) -> ListRequest | None:
    number = _int(page)
    if filt not in {f.value for f in Filter} or number is None:
        return None
    return ListRequest(Filter(filt), number)


def parse(data: str) -> ListRequest | GameRequest | None:
    if not data.startswith(PREFIX):
        return None
    action, *fields = data.removeprefix(PREFIX).split(":")
    if action == "l" and len(fields) == 2:
        return _list(*fields)
    if action == "g" and len(fields) == 4:
        game_id, guess_page = _int(fields[0]), _int(fields[3])
        back = _list(fields[1], fields[2])
        if game_id is None or guess_page is None or back is None:
            return None
        return GameRequest(game_id, back, guess_page)
    return None
