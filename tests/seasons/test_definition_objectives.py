from dataclasses import replace

from nani_pix_bot.seasons import registry
from nani_pix_bot.seasons.definition import (
    Goal,
    GoalKind,
    Objective,
    objective_problems,
    run_problems,
)


def _demo():
    return registry.discover("tests.seasons.fake_runs")["demo_1"]


def _objective(key="extra", **goal) -> Objective:
    names = {"EN": "Extra", "RU": "Ещё"}
    return Objective(key, names, Goal(GoalKind.WINS, **(goal or {"target": 1})), xp=10)


def test_the_demo_objectives_are_valid() -> None:
    assert len(_demo().objectives) == 4
    assert objective_problems(_demo()) == []


def test_no_objectives_is_fine() -> None:
    assert objective_problems(replace(_demo(), objectives=())) == []


def test_fewer_than_four_is_a_problem() -> None:
    assert objective_problems(replace(_demo(), objectives=_demo().objectives[:3]))


def test_more_than_eight_is_a_problem() -> None:
    many = tuple(_objective(f"o{i}") for i in range(9))
    assert objective_problems(replace(_demo(), objectives=many))


def test_run_problems_include_objectives() -> None:
    assert run_problems(replace(_demo(), objectives=_demo().objectives[:3]))


def test_keys_are_unique_and_short_snake_case() -> None:
    four = _demo().objectives
    for bad in (four[0].key, "Bad Key", "x" * 33):
        objectives = (*four[:3], replace(four[3], key=bad))
        assert objective_problems(replace(_demo(), objectives=objectives))


def test_amounts_and_targets_must_be_positive() -> None:
    four = _demo().objectives
    for bad in (replace(four[3], xp=0), replace(four[3], coins=-1), _objective(target=0)):
        assert objective_problems(replace(_demo(), objectives=(*four[:3], bad)))


def test_a_stage_limit_only_for_wins_and_within_1_to_5() -> None:
    four = _demo().objectives
    bad_kind = Objective("x", {"EN": "X"}, Goal(GoalKind.HOSTED, 1, max_stage=2), xp=5)
    assert objective_problems(replace(_demo(), objectives=(*four[:3], bad_kind)))
    too_late = _objective(target=1, max_stage=6)
    assert objective_problems(replace(_demo(), objectives=(*four[:3], too_late)))


def test_a_name_falls_back_to_english() -> None:
    objective = Objective("x", {"EN": "X"}, Goal(GoalKind.HOSTED, 1), xp=5)
    assert objective.name("RU") == "X"
