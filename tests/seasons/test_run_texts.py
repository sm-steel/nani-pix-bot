"""Every player-facing text a run declares exists in both shipped languages:
a blank RU name would fall back silently and an empty one shows nothing."""

from collections.abc import Iterator, Mapping
from dataclasses import replace
from types import MappingProxyType

from nani_pix_bot.seasons import registry
from nani_pix_bot.seasons.definition import SeasonRun

LANGS = ("EN", "RU")


def _texts(run: SeasonRun) -> Iterator[tuple[str, Mapping[str, str]]]:
    yield "names", run.names
    if run.gate is not None:
        yield "gate.description", run.gate.description
    for objective in run.objectives:
        yield f"objective {objective.key}", objective.names
    for item in run.items:
        yield f"item {item.key}", item.names
    for achievement in run.achievements:
        yield f"achievement {achievement.key}", achievement.names
        yield f"achievement {achievement.key} description", achievement.descriptions


def _missing(runs: Mapping[str, SeasonRun]) -> list[str]:
    """Not parametrized over the runs: with zero shipped runs this still runs
    (and passes) instead of collecting nothing."""
    return [
        f"{run_id}: {where} [{lang}]"
        for run_id, run in runs.items()
        for where, texts in _texts(run)
        for lang in LANGS
        if not (texts.get(lang) or "").strip()
    ]


def test_every_shipped_run_names_everything_in_both_languages() -> None:
    registry.all_runs.cache_clear()
    assert _missing(registry.all_runs()) == []


def test_every_fake_run_names_everything_in_both_languages() -> None:
    assert _missing(registry.discover("tests.seasons.fake_runs")) == []


def test_a_blank_text_is_reported() -> None:
    run = registry.discover("tests.seasons.fake_runs")["demo_1"]
    blank = replace(run, names=MappingProxyType({"EN": "Demo", "RU": " "}))
    assert _missing({"demo_1": blank}) == ["demo_1: names [RU]"]
