from dataclasses import replace

import pytest

from nani_pix_bot.seasons import registry
from nani_pix_bot.seasons.definition import Reward, Tier, pass_problems


@pytest.fixture
def demo():
    runs = registry.discover("tests.seasons.fake_runs")
    return runs["demo_1"]


def test_the_demo_pass_is_valid(demo) -> None:
    assert pass_problems(demo) == []


@pytest.mark.parametrize("run", list(registry.all_runs().values()), ids=lambda r: r.run_id)
def test_every_shipped_pass_is_valid(run) -> None:
    assert pass_problems(run) == []


def test_thresholds_must_rise(demo) -> None:
    tiers = (Tier(100, Reward.coins(5)), Tier(100, Reward.coins(5)))
    assert "tier 2 needs more XP than tier 1" in pass_problems(replace(demo, tiers=tiers))


def test_an_item_reward_must_be_one_of_the_runs_items(demo) -> None:
    tiers = (Tier(10, Reward.item("name_flair.nope")),)
    assert any("name_flair.nope" in p for p in pass_problems(replace(demo, tiers=tiers)))


def test_at_most_two_clue_token_tiers(demo) -> None:
    tiers = tuple(Tier(10 * (i + 1), Reward.clue_ticket()) for i in range(3))
    assert any("clue token" in p for p in pass_problems(replace(demo, tiers=tiers)))


def test_premium_rewards_need_a_price(demo) -> None:
    tiers = (Tier(10, premium=Reward.coins(5)),)
    assert any(
        "premium_price" in p for p in pass_problems(replace(demo, tiers=tiers, premium_price=0))
    )


def test_amounts_must_be_positive(demo) -> None:
    tiers = (Tier(10, Reward.coins(0)),)
    assert any("amount" in p for p in pass_problems(replace(demo, tiers=tiers)))
