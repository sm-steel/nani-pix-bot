"""What a season run declares (seasons spec §1, §5, §7). Pure data: the
services read it, nothing here does anything."""

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Literal

from nani_pix_bot.models.enums import CosmeticSlot

TagKind = Literal["genre", "theme"]


@dataclass(frozen=True)
class GateTag:
    """One accepted MAL genre or theme. Matched by `mal_id` against Tenrai's
    tags, by `kind` + `name` against Shikimori's (whose ids differ from MAL's
    for some tags — spec §5, P0). `shikimori_id` drives the bot's random-pick
    filter; None means that tag can't be filtered on Shikimori."""

    kind: TagKind
    name: str
    mal_id: int
    shikimori_id: int | None = None


@dataclass(frozen=True)
class Gate:
    """A game qualifies if its anime has ANY of these tags. `description`
    is the player-facing rule per language, naming every tag."""

    any_of: tuple[GateTag, ...]
    description: Mapping[str, str]


@dataclass(frozen=True)
class XpTable:
    win_by_stage: tuple[int, int, int, int, int]
    host_solved: int
    host_unsolved: int
    first_guess: int


@dataclass(frozen=True)
class CoinTable:
    """🪙 per season-game action (seasons spec §7): a win pays the same at any stage."""

    win: int = 0
    host_solved: int = 0
    host_unsolved: int = 0
    first_guess: int = 0

    def as_xp_table(self) -> XpTable:
        """The same shape as the XP table, so xp.awards can price coins too."""
        return XpTable(
            win_by_stage=(self.win, self.win, self.win, self.win, self.win),
            host_solved=self.host_solved,
            host_unsolved=self.host_unsolved,
            first_guess=self.first_guess,
        )


@dataclass(frozen=True)
class Item:
    """One cosmetic a run offers (seasons spec §3). `key` is `<slot>.<name>`;
    its full id is `<run_id>/<key>`. `look` means something per slot (an
    emoji, a frame/overlay key, a RevealEffect name, a banner file) —
    services/cosmetics/looks.py validates it."""

    key: str
    slot: CosmeticSlot
    look: str
    names: Mapping[str, str]

    def name(self, lang: str) -> str:
        return self.names.get(lang) or self.names["EN"]


@dataclass(frozen=True)
class SeasonRun:
    run_id: str
    names: Mapping[str, str]
    xp: XpTable
    gate: Gate | None = None
    items: tuple[Item, ...] = ()
    coins: CoinTable = CoinTable()

    def name(self, lang: str) -> str:
        return self.names.get(lang) or self.names["EN"]
