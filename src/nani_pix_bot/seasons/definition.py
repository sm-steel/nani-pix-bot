"""What a season run declares (seasons spec §1, §5, §7). Pure data: the
services read it, nothing here does anything."""

import enum
import re
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from types import MappingProxyType
from typing import Any, Literal

from nani_pix_bot.models.achievement import KEY_LENGTH as ACHIEVEMENT_KEY_LENGTH
from nani_pix_bot.models.achievement import PERIOD_KEY_LENGTH
from nani_pix_bot.models.enums import CosmeticSlot, EventType, Rarity
from nani_pix_bot.models.season import OBJECTIVE_KEY_LENGTH

TagKind = Literal["genre", "theme"]


class RewardKind(enum.StrEnum):
    ITEM = "item"  # a cosmetic from the run's items
    COINS = "coins"  # the season's 🪙
    CURRENCY = "currency"  # 💠
    # 🎟 the clue token; named so a security linter doesn't read "token" as a secret
    CLUE_TICKET = "clue_token"
    STAMP = "stamp"  # the season stamp's gold ring (free track); the claim is the reward
    STAMP_PLUS = "stamp_plus"  # the gold season stamp art (premium track)


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

    @classmethod
    def stamp(cls) -> "Reward":
        return cls(RewardKind.STAMP)

    @classmethod
    def stamp_plus(cls) -> "Reward":
        return cls(RewardKind.STAMP_PLUS)


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
    is the player-facing rule per language, naming every tag.

    Its shape is fixed, because it is slotted into one sentence: a plural
    noun phrase listing the tags, lowercase, joined with "or" and no
    trailing period. The templates are "This season only {rule} can be
    played." (EN) and «…можно играть только в {rule}.» (RU), so the RU
    phrase follows «в» in the accusative. For example:
    EN "slice-of-life, romance or school anime",
    RU «повседневность, романтику или школу»."""

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


class GoalKind(enum.StrEnum):
    """What an objective counts (spec §5). Progress lives in services/seasons/goals.py."""

    HOSTED = "hosted"
    WINS = "wins"
    GUESSED_IN = "guessed_in"
    SHARED_CLUES = "shared_clues"
    TIPS = "tips"
    DAYS_PLAYED = "days_played"


@dataclass(frozen=True)
class Goal:
    kind: GoalKind
    target: int
    max_stage: int | None = None  # WINS only: count wins by this stage


@dataclass(frozen=True)
class Objective:
    """A one-time season objective (spec §5): reaching `goal` pays `xp` Season XP and `coins` 🪙."""

    key: str
    names: Mapping[str, str]
    goal: Goal
    xp: int
    coins: int = 0

    def name(self, lang: str) -> str:
        return self.names.get(lang) or self.names["EN"]


MAX_RUN_ACHIEVEMENTS = 4
MAX_RUN_ID_LENGTH = PERIOD_KEY_LENGTH  # the run id is the per-season grant period_key


@dataclass(frozen=True)
class RunAchievement:
    """An achievement a run module declares for its own season (spec §6). It is
    merged into the catalogue as `"{run_id}.{key}"`. `progress` takes a
    SeasonHistory and returns the number reached; the text comes from these
    EN/RU maps (EN is the fallback), and descriptions take no placeholders."""

    key: str
    names: Mapping[str, str]
    descriptions: Mapping[str, str]
    rarity: Rarity
    triggers: frozenset[EventType]
    progress: Callable[[Any], int]
    hidden: bool = False

    def name(self, lang: str) -> str:
        return self.names.get(lang) or self.names["EN"]

    def description(self, lang: str) -> str:
        return self.descriptions.get(lang) or self.descriptions["EN"]


MIN_OBJECTIVES = 4
MAX_OBJECTIVES = 8
_STAGES = 5
_KEY_SHAPE = re.compile(r"[a-z0-9_]+")


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
    objectives: tuple[Objective, ...] = ()
    achievements: tuple[RunAchievement, ...] = ()

    def name(self, lang: str) -> str:
        return self.names.get(lang) or self.names["EN"]


def _reward_problems(run: SeasonRun, number: int, reward: Reward) -> list[str]:
    keys = {item.key for item in run.items}
    if reward.kind is RewardKind.ITEM and reward.item_key not in keys:
        return [f"tier {number}: {reward.item_key!r} is not one of {run.run_id}'s items"]
    if reward.kind in (RewardKind.COINS, RewardKind.CURRENCY) and reward.amount <= 0:
        return [f"tier {number}: a {reward.kind.value} reward needs a positive amount"]
    return []


def _stamp_problems(run: SeasonRun) -> list[str]:
    """The stamp rewards: one gold ring on the free track, one plus art on the
    premium track, and no plus art without the ring."""
    free = [t.free.kind for t in run.tiers if t.free is not None]
    premium = [t.premium.kind for t in run.tiers if t.premium is not None]
    rings = free.count(RewardKind.STAMP)
    pluses = premium.count(RewardKind.STAMP_PLUS)
    checks = (
        (rings > 1, "at most one stamp reward per pass"),
        (pluses > 1, "at most one stamp_plus reward per pass"),
        (RewardKind.STAMP in premium, "stamp only on the free track"),
        (RewardKind.STAMP_PLUS in free, "stamp_plus only on the premium track"),
        (pluses > 0 and rings == 0, "stamp_plus needs a stamp somewhere on the free track"),
    )
    return [message for failed, message in checks if failed]


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
    return problems + _stamp_problems(run)


def _pass_item_keys(run: SeasonRun) -> set[str]:
    """The items the pass gives on either track."""
    return {
        reward.item_key
        for tier in run.tiers
        for reward in (tier.free, tier.premium)
        if reward is not None and reward.kind is RewardKind.ITEM
    }


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
        if offer.item_key in _pass_item_keys(run):
            problems.append(f"{where}: already a pass reward; an item is shop- or pass-only")
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


def _goal_problems(where: str, goal: Goal) -> list[str]:
    problems = []
    if goal.target < 1:
        problems.append(f"{where}: target must be positive")
    stage = goal.max_stage
    if stage is not None and (goal.kind is not GoalKind.WINS or not 1 <= stage <= _STAGES):
        problems.append(f"{where}: max_stage only for wins, 1..{_STAGES}")
    return problems


def _objective_problems(objective: Objective) -> list[str]:
    where = f"objective {objective.key!r}"
    problems = _goal_problems(where, objective.goal)
    if not _KEY_SHAPE.fullmatch(objective.key) or len(objective.key) > OBJECTIVE_KEY_LENGTH:
        problems.append(f"{where}: key must be snake_case, ≤{OBJECTIVE_KEY_LENGTH} chars")
    if objective.xp < 1 or objective.coins < 0:
        problems.append(f"{where}: xp must be positive, coins not negative")
    return problems


def objective_problems(run: SeasonRun) -> list[str]:
    count = len(run.objectives)
    problems = [p for o in run.objectives for p in _objective_problems(o)]
    if count and not MIN_OBJECTIVES <= count <= MAX_OBJECTIVES:
        problems.append(f"objectives: {MIN_OBJECTIVES}..{MAX_OBJECTIVES} per run, got {count}")
    keys = [o.key for o in run.objectives]
    if len(keys) != len(set(keys)):
        problems.append("objectives: a key appears twice")
    return problems


def _achievement_problems(run: SeasonRun, achievement: RunAchievement) -> list[str]:
    where = f"achievement {achievement.key!r}"
    problems = []
    longest = ACHIEVEMENT_KEY_LENGTH - len(run.run_id) - 1
    if not _KEY_SHAPE.fullmatch(achievement.key) or len(achievement.key) > longest:
        problems.append(f"{where}: key must be snake_case, ≤{longest} chars")
    for label, texts in (("names", achievement.names), ("descriptions", achievement.descriptions)):
        if not all(texts.get(lang) for lang in ("EN", "RU")):
            problems.append(f"{where}: {label} need EN and RU text")
    if not achievement.triggers:
        problems.append(f"{where}: needs at least one trigger event")
    return problems


def achievement_problems(run: SeasonRun) -> list[str]:
    count = len(run.achievements)
    problems = [p for a in run.achievements for p in _achievement_problems(run, a)]
    if count > MAX_RUN_ACHIEVEMENTS:
        problems.append(f"achievements: at most {MAX_RUN_ACHIEVEMENTS} per run, got {count}")
    keys = [a.key for a in run.achievements]
    if len(keys) != len(set(keys)):
        problems.append("achievements: a key appears twice")
    return problems


def run_problems(run: SeasonRun) -> list[str]:
    """Everything wrong with a run module's data — checked when the registry loads it."""
    return (
        pass_problems(run)
        + shop_problems(run)
        + modifier_problems(run)
        + objective_problems(run)
        + achievement_problems(run)
    )
