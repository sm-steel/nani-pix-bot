"""What a season run declares (seasons spec §1, §5, §7). Pure data: the
services read it, nothing here does anything."""

import enum
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Literal

from nani_pix_bot.models.enums import CosmeticSlot

TagKind = Literal["genre", "theme"]


class RewardKind(enum.StrEnum):
    ITEM = "item"  # a cosmetic from the run's items
    COINS = "coins"  # the season's 🪙
    CURRENCY = "currency"  # 💠
    # 🎟 the clue token; named so a security linter doesn't read "token" as a secret
    CLUE_TICKET = "clue_token"


@dataclass(frozen=True)
class Reward:
    """One pass reward. `item_key` is a key of the run's items (ITEM); `amount`
    is the 🪙/💠 paid (COINS/CURRENCY); a CLUE_TICKET reward gives one token."""

    kind: RewardKind
    item_key: str = ""
    amount: int = 0

    @classmethod
    def item(cls, key: str) -> "Reward":
        return cls(RewardKind.ITEM, item_key=key)

    @classmethod
    def coins(cls, amount: int) -> "Reward":
        return cls(RewardKind.COINS, amount=amount)

    @classmethod
    def currency(cls, amount: int) -> "Reward":
        return cls(RewardKind.CURRENCY, amount=amount)

    @classmethod
    def clue_ticket(cls) -> "Reward":
        return cls(RewardKind.CLUE_TICKET)


@dataclass(frozen=True)
class Tier:
    """Reached at `xp` Season XP: `free` for everyone, `premium` with Premium."""

    xp: int
    free: Reward | None = None
    premium: Reward | None = None


MAX_CLUE_TOKEN_TIERS = 2


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
    tiers: tuple[Tier, ...] = ()
    premium_price: int = 0  # 💠
    coins_per_currency: int = 2  # 🪙 per 1 💠 at conversion

    def name(self, lang: str) -> str:
        return self.names.get(lang) or self.names["EN"]


def _reward_problems(run: SeasonRun, number: int, reward: Reward) -> list[str]:
    keys = {item.key for item in run.items}
    if reward.kind is RewardKind.ITEM and reward.item_key not in keys:
        return [f"tier {number}: {reward.item_key!r} is not one of {run.run_id}'s items"]
    if reward.kind in (RewardKind.COINS, RewardKind.CURRENCY) and reward.amount <= 0:
        return [f"tier {number}: a {reward.kind.value} reward needs a positive amount"]
    return []


def pass_problems(run: SeasonRun) -> list[str]:
    """Everything wrong with `run`'s pass; [] when it's valid."""
    problems: list[str] = []
    if run.coins_per_currency < 1:
        problems.append("coins_per_currency must be at least 1")
    for number, tier in enumerate(run.tiers, start=1):
        if tier.xp <= 0:
            problems.append(f"tier {number} needs a positive XP threshold")
        if number > 1 and tier.xp <= run.tiers[number - 2].xp:
            problems.append(f"tier {number} needs more XP than tier {number - 1}")
        for reward in (tier.free, tier.premium):
            if reward is not None:
                problems += _reward_problems(run, number, reward)
    tokens = sum(
        1
        for tier in run.tiers
        for reward in (tier.free, tier.premium)
        if reward is not None and reward.kind is RewardKind.CLUE_TICKET
    )
    if tokens > MAX_CLUE_TOKEN_TIERS:
        problems.append(f"{tokens} clue token rewards; at most {MAX_CLUE_TOKEN_TIERS} per pass")
    if any(tier.premium is not None for tier in run.tiers) and run.premium_price <= 0:
        problems.append("premium rewards need a premium_price")
    return problems
