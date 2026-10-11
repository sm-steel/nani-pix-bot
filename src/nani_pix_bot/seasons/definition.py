"""What a season run declares (seasons spec §1, §5, §7). Pure data: the
services read it, nothing here does anything."""

import enum
from collections.abc import Mapping
from dataclasses import dataclass, field
from types import MappingProxyType
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
MAX_SHOP_CLUE_TOKENS = 2
_SHOP_KINDS = (RewardKind.ITEM, RewardKind.CLUE_TICKET)


@dataclass(frozen=True)
class ShopOffer:
    """One Season Shop line (spec §4): a cosmetic sold once, or the clue
    token sold up to `limit` times per season, priced in 🪙."""

    kind: RewardKind
    item_key: str = ""
    price: int = 0
    limit: int = 1

    @classmethod
    def item(cls, key: str, price: int) -> "ShopOffer":
        return cls(RewardKind.ITEM, item_key=key, price=price)

    @classmethod
    def clue_ticket(cls, price: int, limit: int) -> "ShopOffer":
        return cls(RewardKind.CLUE_TICKET, price=price, limit=limit)

    @property
    def key(self) -> str:
        return self.item_key if self.kind is RewardKind.ITEM else self.kind.value


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


class Modifier(enum.StrEnum):
    """Rule modifiers a run may set (spec §5). Only what a run uses is built."""

    CLUE_DISCOUNT_PCT = "clue_discount_pct"


MAX_CLUE_DISCOUNT_PCT = 90


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
    shop: tuple[ShopOffer, ...] = ()
    modifiers: Mapping[Modifier, int] = field(default_factory=lambda: MappingProxyType({}))
    style: str | None = None  # a key into services.seasons.styles.STYLES

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


def _offer_problems(run: SeasonRun, offer: ShopOffer) -> list[str]:
    where = f"shop offer {offer.key!r}"
    problems = []
    if offer.kind not in _SHOP_KINDS:
        problems.append(f"{where}: only items and the clue token are sold")
    if offer.price <= 0:
        problems.append(f"{where}: price must be positive")
    if offer.kind is RewardKind.ITEM:
        if offer.item_key not in {item.key for item in run.items}:
            problems.append(f"{where}: no such item")
        if offer.limit != 1:
            problems.append(f"{where}: an item sells once")
    if offer.kind is RewardKind.CLUE_TICKET and not 1 <= offer.limit <= MAX_SHOP_CLUE_TOKENS:
        problems.append(f"{where}: limit must be 1..{MAX_SHOP_CLUE_TOKENS}")
    return problems


def shop_problems(run: SeasonRun) -> list[str]:
    problems = [p for offer in run.shop for p in _offer_problems(run, offer)]
    keys = [offer.key for offer in run.shop]
    if len(keys) != len(set(keys)):
        problems.append("shop: an offer appears twice")
    return problems


def modifier_problems(run: SeasonRun) -> list[str]:
    discount = run.modifiers.get(Modifier.CLUE_DISCOUNT_PCT)
    if discount is not None and not 1 <= discount <= MAX_CLUE_DISCOUNT_PCT:
        return [f"modifier clue_discount_pct must be 1..{MAX_CLUE_DISCOUNT_PCT}"]
    return []


def run_problems(run: SeasonRun) -> list[str]:
    """Everything wrong with a run module's data — checked when the registry loads it."""
    return pass_problems(run) + shop_problems(run) + modifier_problems(run)
