"""The Sakura Season run module: valid, and its economy holds the targets the
user approved. Every number is read from RUN, never re-typed."""

import json
from pathlib import Path

from nani_pix_bot.models.enums import CosmeticSlot
from nani_pix_bot.seasons import registry
from nani_pix_bot.seasons.definition import RewardKind, achievement_problems, run_problems
from nani_pix_bot.seasons.registry import ResolvedItem, discover
from nani_pix_bot.services.cosmetics import looks
from nani_pix_bot.services.seasons import styles

RUN = discover("nani_pix_bot.seasons.runs")["sakura_1"]
SNAPSHOT = Path(__file__).with_name("gate_snapshot.json")


def _xp_and_coins(*, games, wins, win_stage, hosted, solved, objectives):
    """One player's season: first guess in every game guessed in, wins at one
    stage, hosting with `solved` solved, the given objectives done."""
    xp = games * RUN.xp.first_guess + wins * RUN.xp.win_by_stage[win_stage - 1]
    xp += solved * RUN.xp.host_solved + (hosted - solved) * RUN.xp.host_unsolved
    coins = games * RUN.coins.first_guess + wins * RUN.coins.win
    coins += solved * RUN.coins.host_solved + (hosted - solved) * RUN.coins.host_unsolved
    xp += sum(o.xp for o in objectives)
    coins += sum(o.coins for o in objectives)
    return xp, coins


def _free_coins_reached(xp: int) -> int:
    rewards = [t.free for t in RUN.tiers if t.xp <= xp]
    return sum(r.amount for r in rewards if r is not None and r.kind is RewardKind.COINS)


def _six_of_seven():
    return sorted(RUN.objectives, key=lambda o: o.coins)[1:]  # skips the cheapest


def test_a_conservative_free_player_can_afford_the_whole_shop() -> None:
    xp, coins = _xp_and_coins(
        games=12, wins=3, win_stage=3, hosted=3, solved=2, objectives=_six_of_seven()
    )
    total = coins + _free_coins_reached(xp)
    shop = sum(o.price * o.limit for o in RUN.shop)
    assert shop == 540
    assert total >= shop + 50


def test_a_typical_regular_finishes_the_pass() -> None:
    xp, _ = _xp_and_coins(
        games=15, wins=5, win_stage=2, hosted=5, solved=4, objectives=_six_of_seven()
    )
    assert xp >= RUN.tiers[-1].xp


def test_the_run_is_valid_and_the_registry_loads_it() -> None:
    assert run_problems(RUN) == []
    registry.all_runs.cache_clear()
    assert registry.get("sakura_1") == RUN


def test_every_item_has_a_valid_look() -> None:
    resolved = [ResolvedItem(f"sakura_1/{i.key}", RUN, i) for i in RUN.items]
    assert [p for r in resolved for p in looks.problems(r)] == []
    reactions = [i.look for i in RUN.items if i.slot is CosmeticSlot.GUESS_REACTION]
    assert reactions
    assert all(look in looks.ALLOWED_REACTIONS for look in reactions)


def test_its_style_exists() -> None:
    assert styles.problems(RUN) == []


def test_clue_tickets_come_from_free_tier_3_and_the_shop() -> None:
    tiers = [
        (number, track)
        for number, tier in enumerate(RUN.tiers, start=1)
        for track, reward in (("free", tier.free), ("premium", tier.premium))
        if reward is not None and reward.kind is RewardKind.CLUE_TICKET
    ]
    assert tiers == [(3, "free")]
    offers = [o for o in RUN.shop if o.kind is RewardKind.CLUE_TICKET]
    assert [o.limit for o in offers] == [2]


def test_no_shop_item_is_a_pass_reward() -> None:
    passed = {
        reward.item_key
        for tier in RUN.tiers
        for reward in (tier.free, tier.premium)
        if reward is not None and reward.kind is RewardKind.ITEM
    }
    sold = {o.item_key for o in RUN.shop if o.kind is RewardKind.ITEM}
    assert not passed & sold
    assert passed | sold == {i.key for i in RUN.items}


def test_its_run_achievements_validate() -> None:
    assert [a.key for a in RUN.achievements] == ["kokuhaku", "hanami_party", "full_bloom"]
    assert achievement_problems(RUN) == []


def test_every_shipped_gate_tag_is_in_the_snapshot() -> None:
    snapshot = json.loads(SNAPSHOT.read_text(encoding="utf-8"))
    registry.all_runs.cache_clear()
    tags = [t for run in registry.all_runs().values() if run.gate for t in run.gate.any_of]
    assert tags
    for tag in tags:
        assert snapshot[tag.kind].get(str(tag.mal_id)) == tag.name, tag
