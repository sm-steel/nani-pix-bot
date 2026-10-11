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


def _xp_and_coins(*, games, win_stages, hosted, solved, objectives):
    """One player's season: first guess in every game guessed in, a win at each
    of `win_stages`, hosting with `solved` solved, the given objectives done."""
    xp = games * RUN.xp.first_guess + sum(RUN.xp.win_by_stage[s - 1] for s in win_stages)
    xp += solved * RUN.xp.host_solved + (hosted - solved) * RUN.xp.host_unsolved
    coins = games * RUN.coins.first_guess + len(win_stages) * RUN.coins.win
    coins += solved * RUN.coins.host_solved + (hosted - solved) * RUN.coins.host_unsolved
    xp += sum(o.xp for o in objectives)
    coins += sum(o.coins for o in objectives)
    return xp, coins


def _free_coins_reached(xp: int) -> int:
    rewards = [t.free for t in RUN.tiers if t.xp <= xp]
    return sum(r.amount for r in rewards if r is not None and r.kind is RewardKind.COINS)


def _six_of_seven():
    return sorted(RUN.objectives, key=lambda o: o.coins)[1:]  # skips the cheapest


def _objective(key: str):
    return next(o for o in RUN.objectives if o.key == key)


def test_a_conservative_free_player_can_afford_the_whole_shop() -> None:
    win_stages = (2, 3, 3)
    done = _six_of_seven()
    # Internally consistent: First Bloom is among the objectives, so a win is early enough.
    bloom = _objective("first_bloom")
    assert bloom in done
    assert bloom.goal.max_stage is not None
    assert min(win_stages) <= bloom.goal.max_stage
    xp, coins = _xp_and_coins(games=12, win_stages=win_stages, hosted=3, solved=2, objectives=done)
    total = coins + _free_coins_reached(xp)
    shop = sum(o.price * o.limit for o in RUN.shop)
    assert shop == 540
    assert total >= shop + 50


def test_a_typical_regular_finishes_the_pass() -> None:
    xp, _ = _xp_and_coins(
        games=15, win_stages=(2,) * 5, hosted=5, solved=4, objectives=_six_of_seven()
    )
    assert xp >= RUN.tiers[-1].xp


def test_the_russian_texts() -> None:
    assert _objective("first_bloom").names["RU"] == "Первое цветение"
    party = next(a for a in RUN.achievements if a.key == "hanami_party")
    assert party.descriptions["RU"] == (
        "Сыграйте в игру сезона, в которой угадывали 4 и более игроков"
    )


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
