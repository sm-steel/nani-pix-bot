from dataclasses import replace

from nani_pix_bot.seasons import registry
from nani_pix_bot.seasons.definition import Reward, RewardKind, Tier, pass_problems


def _demo():
    return registry.discover("tests.seasons.fake_runs")["demo_1"]


def _with(*tiers: Tier):
    return replace(_demo(), tiers=tiers)


def test_the_stamp_rewards() -> None:
    assert Reward.stamp().kind is RewardKind.STAMP
    assert Reward.stamp_plus().kind is RewardKind.STAMP_PLUS
    assert RewardKind.STAMP.value == "stamp"
    assert RewardKind.STAMP_PLUS.value == "stamp_plus"


def test_the_demo_ends_in_the_stamp_pair() -> None:
    last = _demo().tiers[-1]
    assert (last.free, last.premium) == (Reward.stamp(), Reward.stamp_plus())
    assert pass_problems(_demo()) == []


def test_two_stamps_are_a_problem() -> None:
    run = _with(Tier(10, Reward.stamp()), Tier(20, Reward.stamp()))
    assert pass_problems(run) == ["at most one stamp reward per pass"]


def test_a_premium_stamp_is_a_problem() -> None:
    run = _with(Tier(10, Reward.coins(5), Reward.stamp()))
    assert any("stamp only on the free track" in p for p in pass_problems(run))


def test_two_plus_stamps_are_a_problem() -> None:
    run = _with(Tier(10, Reward.stamp(), Reward.stamp_plus()), Tier(20, None, Reward.stamp_plus()))
    assert any("at most one stamp_plus" in p for p in pass_problems(run))


def test_a_free_plus_stamp_is_a_problem() -> None:
    run = _with(Tier(10, Reward.stamp_plus()))
    assert any("stamp_plus only on the premium track" in p for p in pass_problems(run))


def test_a_plus_stamp_needs_a_stamp() -> None:
    run = _with(Tier(10, Reward.coins(5), Reward.stamp_plus()))
    assert any("stamp_plus needs a stamp" in p for p in pass_problems(run))
