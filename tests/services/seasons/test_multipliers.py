from dataclasses import replace

import pytest

from nani_pix_bot.models.enums import XpSource
from nani_pix_bot.seasons import registry
from nani_pix_bot.seasons.definition import SeasonRun
from nani_pix_bot.services.seasons.multipliers import coin_multiplier, xp_multiplier


def _run(run_id: str) -> SeasonRun:
    run = registry.get(run_id)
    assert run is not None
    return run


@pytest.fixture
def demo1() -> SeasonRun:
    return _run("demo_1")


@pytest.fixture
def demo3() -> SeasonRun:
    return _run("demo_3")


def test_no_multiplier_outside_haunt_event_and_boss(demo1, tagged_game) -> None:
    assert coin_multiplier(demo1, tagged_game) == 1
    assert xp_multiplier(demo1, tagged_game) == 1


def test_haunted_game_doubles_coins_for_everyone(demo3, haunted_game) -> None:
    haunted_game.haunt_twist = "yurei_double"
    sources = (XpSource.WIN, XpSource.HOST, XpSource.FIRST_GUESS)
    assert {coin_multiplier(demo3, haunted_game, s) for s in sources} == {
        demo3.haunt.coin_multiplier
    }
    assert xp_multiplier(demo3, haunted_game) == 1


def test_an_unhaunted_game_of_a_haunt_run_pays_normally(demo3, haunted_game) -> None:
    assert coin_multiplier(demo3, haunted_game) == 1


def test_multipliers_stack(demo3, haunted_game) -> None:
    run = replace(demo3, event_day=replace(demo3.event_day, coin_multiplier=3))
    haunted_game.haunt_twist = "yurei_double"
    haunted_game.event_day = True
    assert coin_multiplier(run, haunted_game) == demo3.haunt.coin_multiplier * 3


def test_boss_multiplier_never_applies_to_a_host_award(demo3, haunted_game) -> None:
    haunted_game.boss_game = True
    goal = demo3.group_goal
    haunt = demo3.haunt.coin_multiplier
    assert coin_multiplier(demo3, haunted_game, XpSource.WIN) == haunt * goal.boss_coin_multiplier
    assert coin_multiplier(demo3, haunted_game, XpSource.HOST) == haunt
    assert xp_multiplier(demo3, haunted_game, XpSource.HOST) == 1
    assert xp_multiplier(demo3, haunted_game, XpSource.WIN) == goal.boss_xp_multiplier


def test_event_day_flag_without_run_event_day_is_ignored(demo3, haunted_game) -> None:
    run = replace(demo3, event_day=None)
    haunted_game.event_day = True
    assert coin_multiplier(run, haunted_game) == 1
