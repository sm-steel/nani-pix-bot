"""The Yōkai Night run module: valid, every look it names exists, and its
mechanics are switched on the way the plan says."""

from nani_pix_bot.models.enums import ConsumableKind, CosmeticSlot
from nani_pix_bot.seasons import registry
from nani_pix_bot.seasons.definition import RewardKind, achievement_problems, run_problems
from nani_pix_bot.seasons.mechanics import (
    DEFAULT_PALETTE,
    BoneSet,
    Haunt,
    HauntTwist,
    HexKind,
    OmikujiKind,
)
from nani_pix_bot.seasons.registry import ResolvedItem, discover
from nani_pix_bot.services.cosmetics import looks
from nani_pix_bot.services.seasons import styles

RUN = discover("nani_pix_bot.seasons.runs")["yokai_1"]
PASS_XP = [20, 50, 90, 140, 190, 240, 300, 360, 420, 490, 570, 650]


def _pass_rewards():
    """(tier number, track, reward) for every reward of the pass."""
    return [
        (number, track, reward)
        for number, tier in enumerate(RUN.tiers, start=1)
        for track, reward in (("free", tier.free), ("premium", tier.premium))
        if reward is not None
    ]


def _pass_items() -> set[str]:
    return {r.item_key for _n, _t, r in _pass_rewards() if r.kind is RewardKind.ITEM}


def _shop_items() -> set[str]:
    return {o.item_key for o in RUN.shop if o.kind is RewardKind.ITEM}


def test_the_run_is_valid_and_the_registry_loads_it() -> None:
    assert run_problems(RUN) == []
    registry.all_runs.cache_clear()
    assert registry.get("yokai_1") == RUN


def test_every_item_has_a_valid_look() -> None:
    resolved = [ResolvedItem(f"yokai_1/{i.key}", RUN, i) for i in RUN.items]
    assert [p for r in resolved for p in looks.problems(r)] == []
    reactions = {i.look for i in RUN.items if i.slot is CosmeticSlot.GUESS_REACTION}
    assert reactions == {"👻", "🎃", "😈"}
    assert all(look in looks.ALLOWED_REACTIONS for look in reactions)


def test_its_style_exists() -> None:
    assert styles.problems(RUN) == []


def test_no_flair_is_the_hex_marker() -> None:
    assert "💀" not in {i.look for i in RUN.items if i.slot is CosmeticSlot.NAME_FLAIR}


def test_the_season_look() -> None:
    assert (RUN.emoji, RUN.mascot_costume, RUN.style) == ("🎃", "pumpkin", "moonlit")
    assert RUN.palette != DEFAULT_PALETTE
    assert dict(RUN.modifiers) == {}


def test_every_item_has_exactly_one_source() -> None:
    assert RUN.group_goal is not None
    assert RUN.bone_set is not None
    sources = [
        _pass_items(),
        _shop_items(),
        {RUN.group_goal.contributor_item_key},
        {RUN.bone_set.item_key},
    ]
    assert sum(len(s) for s in sources) == len(RUN.items) == 33
    assert set().union(*sources) == {i.key for i in RUN.items}


def test_the_exclusive_items() -> None:
    assert RUN.group_goal is not None
    assert RUN.group_goal.contributor_item_key == "title.gashadokuro_slayer"
    assert RUN.bone_set == BoneSet(7, "name_flair.mini_skeleton")


def test_consumables_come_from_where_the_plan_says() -> None:
    def tiers_of(kind):
        return [(n, track) for n, track, r in _pass_rewards() if r.kind is kind]

    def shop_limit(kind):
        return [o.limit for o in RUN.shop if o.kind is kind]

    assert tiers_of(RewardKind.CLUE_TICKET) == [(3, "free")]
    assert shop_limit(RewardKind.CLUE_TICKET) == [2]
    assert tiers_of(RewardKind.OMAMORI) == [(7, "free")]
    assert shop_limit(RewardKind.OMAMORI) == []
    daikichi = next(o for o in RUN.omikuji if o.kind is OmikujiKind.DAIKICHI)
    assert daikichi.consumable is ConsumableKind.OMAMORI
    assert tiers_of(RewardKind.OFUDA) == []
    assert shop_limit(RewardKind.OFUDA) == [8]


def test_the_mechanics_are_on() -> None:
    assert RUN.haunt == Haunt(25, tuple(HauntTwist), 2)
    event, goal = RUN.event_day, RUN.group_goal
    assert event is not None
    assert goal is not None
    assert (event.month, event.day, event.all_haunted, event.coin_multiplier) == (10, 31, True, 2)
    assert event.gate is not None
    assert {t.mal_id for t in event.gate.any_of} == {6, 37}
    assert (
        goal.target,
        goal.per_game,
        goal.per_haunted,
        goal.per_event_day,
        goal.milestone_every,
    ) == (100, 1, 2, 3, 25)
    assert {r.kind: r.cooldown_minutes for r in RUN.hexes} == {
        HexKind.NOPPERABO: 30,
        HexKind.CHOCHIN: 30,
        HexKind.KAPPA: 60,
        HexKind.KITSUNE: 60,
    }
    assert [o.kind for o in RUN.omikuji] == list(OmikujiKind)
    assert sum(o.weight for o in RUN.omikuji) == 100


def test_twelve_tiers_with_stamps() -> None:
    assert [t.xp for t in RUN.tiers] == PASS_XP
    stamps = [(n, track, r.kind) for n, track, r in _pass_rewards() if "stamp" in r.kind.value]
    assert stamps == [(10, "premium", RewardKind.STAMP_PLUS), (12, "free", RewardKind.STAMP)]


def test_costumes_are_premium_headliners() -> None:
    costumes = {i.key for i in RUN.items if i.slot is CosmeticSlot.MASCOT_COSTUME}
    assert len(costumes) == 5
    placed = [(n, track) for n, track, r in _pass_rewards() if r.item_key in costumes]
    assert all(track == "premium" for _n, track in placed)
    assert len(placed) == 4
    assert {1, 12} <= {n for n, _track in placed}
    assert len(costumes & _shop_items()) == 1


def test_its_run_achievements_validate() -> None:
    assert [a.key for a in RUN.achievements] == [
        "curse_breaker",
        "hex_master",
        "ghost_hunter",
        "paper_shield",
        "fortune_seeker",
        "candle_snuffer",
        "night_parade",
        "gashadokuro",
        "gashadokuro_bane",
        "daikichi",
        "cursed_luck",
        "hexed_and_happy",
        "hyakki_yagyo",
    ]
    assert achievement_problems(RUN) == []
    hidden = {a.key for a in RUN.achievements if a.hidden}
    assert hidden == {
        "gashadokuro_bane",
        "daikichi",
        "cursed_luck",
        "hexed_and_happy",
        "hyakki_yagyo",
    }


def test_the_russian_texts() -> None:
    assert RUN.names["RU"] == "Ночь ёкаев"
    assert RUN.event_day is not None
    assert RUN.event_day.names["RU"] == "Ночной парад"
    assert RUN.gate is not None
    assert RUN.gate.description["RU"] == "ужасы, мистику, мифологию или вампиров"
    assert RUN.event_day.gate is not None
    assert RUN.event_day.gate.description["RU"] == "мифологию или мистику"
    bane = next(a for a in RUN.achievements if a.key == "gashadokuro_bane")
    assert bane.names["RU"] == "Гроза Гасядокуро"
    assert bane.descriptions["RU"] == "Победите в битве против Гасядокуро"
