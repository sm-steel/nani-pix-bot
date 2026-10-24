from dataclasses import replace
from types import MappingProxyType

import pytest

from nani_pix_bot.seasons import registry
from nani_pix_bot.seasons.definition import (
    GoalKind,
    Reward,
    RewardKind,
    ShopOffer,
    Tier,
    run_problems,
)
from nani_pix_bot.seasons.mechanics import (
    DEFAULT_PALETTE,
    BoneSet,
    HexKind,
    HexRule,
    OmikujiKind,
    OmikujiOutcome,
    problems,
)


def _runs():
    return registry.discover("tests.seasons.fake_runs")


def _demo1():
    return _runs()["demo_1"]


def _demo3():
    return _runs()["demo_3"]


def test_a_run_without_the_new_fields_is_valid_and_off() -> None:
    run = _demo1()
    assert (run.haunt, run.event_day, run.group_goal, run.bone_set) == (None,) * 4
    assert (run.hexes, run.omikuji) == ((), ())
    assert (run.palette, run.emoji) == (DEFAULT_PALETTE, "🌸")
    assert run.mascot_costume is None
    assert run_problems(run) == []


def test_demo_3_sets_every_mechanic_and_is_valid() -> None:
    run = _demo3()
    assert None not in (run.haunt, run.event_day, run.group_goal, run.bone_set)
    assert (len(run.hexes), len(run.omikuji)) == (4, 4)
    assert run_problems(run) == []


def test_the_new_goal_kinds_exist() -> None:
    assert GoalKind.HEXES_BLOCKED == "hexes_blocked"
    assert GoalKind.HAUNTED_WINS == "haunted_wins"


@pytest.mark.parametrize("bad", [0, 101])
def test_haunt_chance_is_a_percent(bad) -> None:
    run = _demo3()
    assert problems(replace(run, haunt=replace(run.haunt, chance_pct=bad)))


def test_haunt_twists_are_non_empty_and_unique() -> None:
    run = _demo3()
    twist = run.haunt.twists[0]
    assert problems(replace(run, haunt=replace(run.haunt, twists=())))
    assert problems(replace(run, haunt=replace(run.haunt, twists=(twist, twist))))


def test_haunt_coin_multiplier_is_at_least_one() -> None:
    run = _demo3()
    assert problems(replace(run, haunt=replace(run.haunt, coin_multiplier=0)))


@pytest.mark.parametrize(("month", "day"), [(2, 30), (13, 1), (0, 5)])
def test_event_day_must_be_a_real_date(month, day) -> None:
    run = _demo3()
    assert problems(replace(run, event_day=replace(run.event_day, month=month, day=day)))


def test_feb_29_is_a_valid_event_day() -> None:
    run = _demo3()
    assert problems(replace(run, event_day=replace(run.event_day, month=2, day=29))) == []


def test_event_day_needs_en_and_ru_names_and_a_real_multiplier() -> None:
    run = _demo3()
    only_en = MappingProxyType({"EN": "Halloween"})
    assert problems(replace(run, event_day=replace(run.event_day, names=only_en)))
    assert problems(replace(run, event_day=replace(run.event_day, coin_multiplier=0)))


def test_all_haunted_event_day_needs_a_haunt() -> None:
    run = replace(_demo3(), haunt=None)
    assert problems(run)
    calm = replace(run, event_day=replace(run.event_day, all_haunted=False))
    assert problems(calm) == []


def test_group_goal_target_and_amounts() -> None:
    run = _demo3()
    assert problems(replace(run, group_goal=replace(run.group_goal, target=0)))
    assert problems(replace(run, group_goal=replace(run.group_goal, per_game=-1)))


def test_contributor_and_bone_items_must_exist() -> None:
    run = _demo3()
    assert problems(replace(run, group_goal=replace(run.group_goal, contributor_item_key="x.no")))
    assert problems(replace(run, bone_set=BoneSet(3, "x.no")))
    assert problems(replace(run, bone_set=BoneSet(0, run.bone_set.item_key)))


def test_an_item_can_have_only_one_exclusive_source() -> None:
    run = _demo3()
    bone = run.bone_set.item_key
    sold = replace(run, shop=(*run.shop, ShopOffer.item(bone, 50)))
    assert any(bone in p for p in problems(sold))
    in_pass = replace(run, tiers=(*run.tiers, Tier(300, Reward.item(bone), None)))
    assert any(bone in p for p in problems(in_pass))
    both = replace(run, group_goal=replace(run.group_goal, contributor_item_key=bone))
    assert any(bone in p for p in problems(both))


def test_duplicate_hex_kinds_are_rejected() -> None:
    run = _demo3()
    assert problems(replace(run, hexes=(*run.hexes, run.hexes[0])))


def test_hex_prices_cooldowns_and_durations() -> None:
    run = replace(_demo3(), omikuji=())
    nope = HexRule(HexKind.NOPPERABO, price=20, cooldown_minutes=30, duration_hours=2)
    for bad in (
        replace(nope, price=0),
        replace(nope, cooldown_minutes=0),
        replace(nope, cooldown_minutes=1441),
        replace(nope, duration_hours=0),
        replace(nope, amount=5),
    ):
        assert problems(replace(run, hexes=(bad,)))
    assert problems(replace(run, hexes=(nope,))) == []


def test_kappa_amount_is_a_percent_and_kitsune_amount_positive() -> None:
    run = replace(_demo3(), omikuji=())
    kappa = HexRule(HexKind.KAPPA, price=30, cooldown_minutes=60, amount=30)
    kitsune = HexRule(HexKind.KITSUNE, price=30, cooldown_minutes=60, amount=10)
    assert problems(replace(run, hexes=(kappa, kitsune))) == []
    assert problems(replace(run, hexes=(replace(kappa, amount=0),)))
    assert problems(replace(run, hexes=(replace(kappa, amount=101),)))
    assert problems(replace(run, hexes=(replace(kitsune, amount=0),)))
    assert problems(replace(run, hexes=(replace(kitsune, duration_hours=1),)))


def test_prank_omikuji_needs_kyo_and_a_prank_hex() -> None:
    run = _demo3()
    kichi_prank = OmikujiOutcome(OmikujiKind.KICHI, 1, prank=True)
    assert problems(replace(run, omikuji=(kichi_prank,)))
    kyo_prank = OmikujiOutcome(OmikujiKind.KYO, 1, prank=True)
    no_prank_hex = tuple(h for h in run.hexes if h.kind in (HexKind.KAPPA, HexKind.KITSUNE))
    assert problems(replace(run, hexes=no_prank_hex, omikuji=(kyo_prank,)))
    assert problems(replace(run, omikuji=(OmikujiOutcome(OmikujiKind.KYO, 0),)))


def test_a_costume_needs_an_emoji() -> None:
    run = _demo3()
    assert problems(replace(run, mascot_costume="witch", emoji=""))
    assert problems(replace(run, mascot_costume="witch")) == []


def test_shop_sells_ofuda_and_omamori_with_a_limit() -> None:
    assert ShopOffer.consumable(RewardKind.OFUDA, 40, 2).key == "ofuda"
    assert ShopOffer.consumable(RewardKind.OMAMORI, 40, 1).key == "omamori"
    run = _demo3()
    too_many = ShopOffer.consumable(RewardKind.OMAMORI, 40, 99)
    assert any("omamori" in p for p in run_problems(replace(run, shop=(*run.shop, too_many))))


def test_the_pass_counts_every_consumable_reward() -> None:
    run = _demo3()
    extra = Tier(300, Reward.ofuda(), None)
    assert any("consumable" in p for p in run_problems(replace(run, tiers=(*run.tiers, extra))))
