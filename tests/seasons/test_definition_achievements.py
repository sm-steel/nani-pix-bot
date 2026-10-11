from dataclasses import replace

import pytest

from nani_pix_bot.models.enums import EventType
from nani_pix_bot.seasons import registry
from nani_pix_bot.seasons.definition import (
    MAX_RUN_ACHIEVEMENTS,
    RunAchievement,
    achievement_problems,
    run_problems,
)


def _demo():
    return registry.discover("tests.seasons.fake_runs")["demo_1"]


def test_the_demo_run_is_valid_and_declares_one() -> None:
    assert len(_demo().achievements) == 1
    assert achievement_problems(_demo()) == []
    assert run_problems(_demo()) == []


def _with(**changes) -> RunAchievement:
    return replace(_demo().achievements[0], **changes)


def _problems(*achievements: RunAchievement) -> list[str]:
    return achievement_problems(replace(_demo(), achievements=achievements))


def test_more_than_the_maximum_is_a_problem() -> None:
    many = tuple(_with(key=f"a{i}") for i in range(MAX_RUN_ACHIEVEMENTS + 1))
    assert _problems(*many)
    assert not _problems(*many[:MAX_RUN_ACHIEVEMENTS])


def test_a_bad_key_shape_is_a_problem() -> None:
    assert _problems(_with(key="Bad Key"))


def test_a_key_too_long_for_the_grant_column_is_a_problem() -> None:
    fits = "x" * (48 - len("demo_1") - 1)
    assert _problems(_with(key=fits)) == []
    assert _problems(_with(key=fits + "x"))


def test_duplicate_keys_are_a_problem() -> None:
    assert _problems(_with(), _with())


def test_a_missing_language_is_a_problem() -> None:
    assert _problems(_with(names={"EN": "Only English"}))
    assert _problems(_with(descriptions={"RU": "Только русский"}))
    assert _problems(_with(names={"EN": "Name", "RU": ""}))


def test_empty_triggers_are_a_problem() -> None:
    assert _problems(_with(triggers=frozenset()))
    assert not _problems(_with(triggers=frozenset({EventType.GUESS})))


def test_run_problems_include_achievements() -> None:
    assert run_problems(replace(_demo(), achievements=(_with(key="Bad Key"),)))


def test_a_run_id_longer_than_the_period_key_is_rejected(tmp_path, monkeypatch) -> None:
    pkg = tmp_path / "long_id_runs"
    pkg.mkdir()
    (pkg / "__init__.py").write_text("")
    run_id = "a_very_long_run_id"
    assert len(run_id) > 16
    (pkg / f"{run_id}.py").write_text(
        "from dataclasses import replace\n"
        "from tests.seasons.fake_runs.demo_1 import RUN as DEMO\n"
        f"RUN = replace(DEMO, run_id='{run_id}')\n"
    )
    monkeypatch.syspath_prepend(str(tmp_path))
    with pytest.raises(registry.RunModuleError, match="at most 16"):
        registry.discover("long_id_runs")
