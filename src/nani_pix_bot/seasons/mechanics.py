"""Optional mechanics a season run can switch on (Halloween season spec): pure
data plus `problems`, the validation `definition.run_problems` appends. Nothing
here does anything; the services read it."""

import datetime
import enum
from collections import Counter
from collections.abc import Mapping
from dataclasses import dataclass
from typing import TYPE_CHECKING

from nani_pix_bot.models.enums import ConsumableKind

if TYPE_CHECKING:
    from nani_pix_bot.seasons.definition import Gate, SeasonRun

RGB = tuple[int, int, int]


@dataclass(frozen=True)
class Palette:
    """The season card's colours."""

    ribbon: RGB
    title_from: RGB
    title_to: RGB


DEFAULT_PALETTE = Palette(
    ribbon=(236, 110, 150), title_from=(255, 160, 190), title_to=(246, 206, 120)
)
DEFAULT_EMOJI = "🌸"


class HauntTwist(enum.StrEnum):
    KITSUNE_MIRROR = "kitsune_mirror"
    TANUKI_LEAF = "tanuki_leaf"
    YUREI_DOUBLE = "yurei_double"
    KYOKOTSU_XRAY = "kyokotsu_xray"


@dataclass(frozen=True)
class Haunt:
    chance_pct: int  # 1..100, rolled once per game at activation
    twists: tuple[HauntTwist, ...]  # non-empty, no duplicates
    coin_multiplier: int = 2  # ≥1, applied to every 🪙 award of a haunted game


@dataclass(frozen=True)
class EventDay:
    month: int
    day: int  # a real calendar day (validated with year 2024, leap-safe)
    names: Mapping[str, str]  # EN/RU required
    gate: "Gate | None" = None  # replaces the run gate on that local day
    coin_multiplier: int = 1  # ≥1
    all_haunted: bool = True  # every game that day is haunted (needs run.haunt)


@dataclass(frozen=True)
class GroupGoal:
    target: int  # ≥1
    per_game: int = 1  # candles per finished (won/unsolved) season game
    per_haunted: int = 2  # replaces per_game for a haunted game
    per_event_day: int = 3  # replaces both for a game on the event day
    milestone_every: int = 25  # 0 = no milestone posts
    boss_xp_multiplier: int = 2
    boss_coin_multiplier: int = 3
    contributor_coins: int = 0
    contributor_item_key: str | None = None  # a key of run.items


class HexKind(enum.StrEnum):
    NOPPERABO = "nopperabo"  # name shown faceless for duration
    CHOCHIN = "chochin"  # reactions to their guesses replaced for duration
    KAPPA = "kappa"  # pct of the target's next win's 🪙 goes to the caster
    KITSUNE = "kitsune"  # the target's next wrong guess costs them `amount` 🪙


PRANK_HEXES = frozenset({HexKind.NOPPERABO, HexKind.CHOCHIN})
MAX_HEX_COOLDOWN_MINUTES = 1440


@dataclass(frozen=True)
class HexRule:
    kind: HexKind
    price: int  # 🪙, ≥1
    cooldown_minutes: int  # caster cooldown for this kind, 1..1440
    duration_hours: int = 0  # NOPPERABO/CHOCHIN: ≥1; KAPPA/KITSUNE: 0 (until triggered)
    amount: int = 0  # KAPPA: percent 1..100; KITSUNE: 🪙 ≥1; pranks: 0


class OmikujiKind(enum.StrEnum):
    DAIKICHI = "daikichi"
    KICHI = "kichi"
    SHOKICHI = "shokichi"
    KYO = "kyo"


@dataclass(frozen=True)
class OmikujiOutcome:
    kind: OmikujiKind
    weight: int  # ≥1
    coins: int = 0  # ≥0
    consumable: ConsumableKind | None = None
    prank: bool = False  # KYO only: a random PRANK_HEXES hex on yourself


@dataclass(frozen=True)
class BoneSet:
    bones: int  # haunted wins needed, ≥1
    item_key: str  # a key of run.items


def _haunt_problems(run: "SeasonRun") -> list[str]:
    haunt = run.haunt
    if haunt is None:
        return []
    checks = (
        (not 1 <= haunt.chance_pct <= 100, "haunt: chance_pct must be 1..100"),
        (not haunt.twists, "haunt: needs at least one twist"),
        (len(haunt.twists) != len(set(haunt.twists)), "haunt: a twist appears twice"),
        (haunt.coin_multiplier < 1, "haunt: coin_multiplier must be at least 1"),
    )
    return [message for failed, message in checks if failed]


def _is_real_day(month: int, day: int) -> bool:
    try:
        datetime.date(2024, month, day)
    except ValueError:
        return False
    return True


def _event_day_problems(run: "SeasonRun") -> list[str]:
    event = run.event_day
    if event is None:
        return []
    named = all(event.names.get(lang) for lang in ("EN", "RU"))
    checks = (
        (not _is_real_day(event.month, event.day), "event_day: not a real calendar day"),
        (not named, "event_day: names need EN and RU"),
        (event.all_haunted and run.haunt is None, "event_day: all_haunted needs run.haunt"),
        (event.coin_multiplier < 1, "event_day: coin_multiplier must be at least 1"),
    )
    return [message for failed, message in checks if failed]


def _item_keys(run: "SeasonRun") -> set[str]:
    return {item.key for item in run.items}


def _group_goal_problems(run: "SeasonRun") -> list[str]:
    goal = run.group_goal
    if goal is None:
        return []
    amounts = (
        goal.per_game,
        goal.per_haunted,
        goal.per_event_day,
        goal.milestone_every,
        goal.boss_xp_multiplier,
        goal.boss_coin_multiplier,
        goal.contributor_coins,
    )
    prize = goal.contributor_item_key
    checks = (
        (goal.target < 1, "group_goal: target must be at least 1"),
        (any(a < 0 for a in amounts), "group_goal: no amount may be negative"),
        (
            prize is not None and prize not in _item_keys(run),
            f"group_goal: {prize!r} is not one of the run's items",
        ),
    )
    return [message for failed, message in checks if failed]


def _hex_problems(rule: HexRule) -> list[str]:
    where = f"hex {rule.kind.value!r}"
    prank = rule.kind in PRANK_HEXES
    checks = (
        (rule.price < 1, f"{where}: price must be at least 1"),
        (
            not 1 <= rule.cooldown_minutes <= MAX_HEX_COOLDOWN_MINUTES,
            f"{where}: cooldown_minutes must be 1..{MAX_HEX_COOLDOWN_MINUTES}",
        ),
        (prank and rule.duration_hours < 1, f"{where}: duration_hours must be at least 1"),
        (not prank and rule.duration_hours != 0, f"{where}: duration_hours must be 0"),
        (prank and rule.amount != 0, f"{where}: amount must be 0"),
        (
            rule.kind is HexKind.KAPPA and not 1 <= rule.amount <= 100,
            f"{where}: amount is a percent, 1..100",
        ),
        (rule.kind is HexKind.KITSUNE and rule.amount < 1, f"{where}: amount must be at least 1"),
    )
    return [message for failed, message in checks if failed]


def _hexes_problems(run: "SeasonRun") -> list[str]:
    problems = [p for rule in run.hexes for p in _hex_problems(rule)]
    kinds = Counter(rule.kind for rule in run.hexes)
    return problems + [f"hexes: {kind.value!r} appears twice" for kind, n in kinds.items() if n > 1]


def _outcome_problems(outcome: OmikujiOutcome, has_prank_hex: bool) -> list[str]:
    where = f"omikuji {outcome.kind.value!r}"
    checks = (
        (outcome.weight < 1, f"{where}: weight must be at least 1"),
        (outcome.coins < 0, f"{where}: coins must not be negative"),
        (outcome.prank and outcome.kind is not OmikujiKind.KYO, f"{where}: only kyo can prank"),
        (outcome.prank and not has_prank_hex, f"{where}: prank needs a prank hex in run.hexes"),
    )
    return [message for failed, message in checks if failed]


def _omikuji_problems(run: "SeasonRun") -> list[str]:
    has_prank_hex = any(rule.kind in PRANK_HEXES for rule in run.hexes)
    return [p for outcome in run.omikuji for p in _outcome_problems(outcome, has_prank_hex)]


def _bone_set_problems(run: "SeasonRun") -> list[str]:
    bones = run.bone_set
    if bones is None:
        return []
    checks = (
        (bones.bones < 1, "bone_set: bones must be at least 1"),
        (
            bones.item_key not in _item_keys(run),
            f"bone_set: {bones.item_key!r} is not one of the run's items",
        ),
    )
    return [message for failed, message in checks if failed]


def _exclusive_problems(run: "SeasonRun") -> list[str]:
    """An item comes from one place only: the pass, the shop, the goal's
    contributor prize or the bone set."""
    prize = run.group_goal.contributor_item_key if run.group_goal else None
    sources = (
        {
            reward.item_key
            for tier in run.tiers
            for reward in (tier.free, tier.premium)
            if reward is not None and reward.item_key
        },
        {offer.item_key for offer in run.shop if offer.item_key},
        {prize} if prize else set(),
        {run.bone_set.item_key} if run.bone_set else set(),
    )
    uses = Counter(key for keys in sources for key in keys)
    return [
        f"item {key!r} has more than one source (pass, shop, goal, bones)"
        for key, n in uses.items()
        if n > 1
    ]


def _look_problems(run: "SeasonRun") -> list[str]:
    if run.mascot_costume is not None and not run.emoji:
        return ["mascot_costume needs a season emoji"]
    return []


def problems(run: "SeasonRun") -> list[str]:
    """Everything wrong with a run's optional mechanics; [] when valid."""
    return (
        _haunt_problems(run)
        + _event_day_problems(run)
        + _group_goal_problems(run)
        + _hexes_problems(run)
        + _omikuji_problems(run)
        + _bone_set_problems(run)
        + _exclusive_problems(run)
        + _look_problems(run)
    )
