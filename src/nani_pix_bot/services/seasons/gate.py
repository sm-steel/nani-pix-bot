"""A season's anime gate (seasons spec §5, P0 notes): a pick qualifies
when its anime has any of the gate's tags — by MAL id for Tenrai's tags,
by kind + name for Shikimori's. No MAL id → unidentifiable (refused);
no provider answering → unavailable (try again), never off-theme."""

import enum
from collections.abc import Sequence
from dataclasses import dataclass

from sqlalchemy.orm import Session

from nani_pix_bot.seasons import registry
from nani_pix_bot.seasons.definition import Gate, GateTag
from nani_pix_bot.services.search.tags import AnimeTag
from nani_pix_bot.services.seasons import schedule
from nani_pix_bot.services.seasons.tags import TagClients, TagsUnavailableError, fetch_tags


class Verdict(enum.StrEnum):
    PASSED = "passed"
    OFF_THEME = "off_theme"
    UNIDENTIFIABLE = "unidentifiable"
    UNAVAILABLE = "unavailable"


REFUSED = frozenset({Verdict.OFF_THEME, Verdict.UNIDENTIFIABLE})


@dataclass(frozen=True)
class GateResult:
    verdict: Verdict
    tags: tuple[AnimeTag, ...] = ()


def _same_tag(want: GateTag, tag: AnimeTag) -> bool:
    if tag.kind != want.kind:
        return False
    if tag.mal_id is not None:
        return tag.mal_id == want.mal_id
    return tag.name.casefold() == want.name.casefold()


def matches(gate: Gate, tags: Sequence[AnimeTag]) -> bool:
    return any(_same_tag(want, tag) for want in gate.any_of for tag in tags)


async def check(
    gate: Gate,
    *,
    mal_id: int | None,
    clients: TagClients,
    known: Sequence[AnimeTag] | None,
) -> GateResult:
    if known is not None:
        tags = tuple(known)
    else:
        if mal_id is None:
            return GateResult(Verdict.UNIDENTIFIABLE)
        try:
            fetched = await fetch_tags(clients, mal_id)
        except TagsUnavailableError:
            return GateResult(Verdict.UNAVAILABLE)
        if fetched is None:
            return GateResult(Verdict.UNIDENTIFIABLE)
        tags = tuple(fetched)
    return GateResult(Verdict.PASSED if matches(gate, tags) else Verdict.OFF_THEME, tags)


def active_gate(session: Session) -> Gate | None:
    season = schedule.active(session)
    run = registry.get(season.run_id) if season is not None else None
    return None if run is None else run.gate
