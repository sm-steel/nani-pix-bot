from dataclasses import replace
from types import MappingProxyType

from nani_pix_bot.seasons import registry
from nani_pix_bot.seasons.definition import MAX_CLUE_DISCOUNT_PCT, Modifier, modifier_problems


def _demo():
    return registry.discover("tests.seasons.fake_runs")["demo_1"]


def test_the_demo_has_a_clue_discount() -> None:
    assert _demo().modifiers[Modifier.CLUE_DISCOUNT_PCT] == 25
    assert modifier_problems(_demo()) == []


def test_a_discount_must_be_a_sane_percent() -> None:
    for bad in (0, -5, MAX_CLUE_DISCOUNT_PCT + 1):
        run = replace(_demo(), modifiers=MappingProxyType({Modifier.CLUE_DISCOUNT_PCT: bad}))
        assert modifier_problems(run)
