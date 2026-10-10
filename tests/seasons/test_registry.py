import pytest

from nani_pix_bot.seasons import registry
from nani_pix_bot.seasons.definition import SeasonRun


def test_discover_finds_every_run_module_by_its_id() -> None:
    runs = registry.discover("tests.seasons.fake_runs")
    assert list(runs) == ["demo_1"]
    assert isinstance(runs["demo_1"], SeasonRun)


def test_run_name_falls_back_to_english() -> None:
    run = registry.discover("tests.seasons.fake_runs")["demo_1"]
    assert run.name("RU") == "Демо-сезон"
    assert run.name("DE") == "Demo Season"


def test_a_module_whose_run_id_differs_from_its_name_is_rejected(tmp_path, monkeypatch) -> None:
    pkg = tmp_path / "bad_runs"
    pkg.mkdir()
    (pkg / "__init__.py").write_text("")
    (pkg / "wrong_1.py").write_text("from tests.seasons.fake_runs.demo_1 import RUN\n")
    monkeypatch.syspath_prepend(str(tmp_path))
    with pytest.raises(registry.RunModuleError, match="must equal the module name"):
        registry.discover("bad_runs")


def test_get_reads_the_shipped_registry(monkeypatch) -> None:
    runs = registry.discover("tests.seasons.fake_runs")
    monkeypatch.setattr(registry, "all_runs", lambda: runs)
    assert registry.get("demo_1") is runs["demo_1"]
    assert registry.get("nope") is None


def test_every_shipped_run_module_loads() -> None:
    registry.all_runs.cache_clear()
    registry.all_runs()  # raises RunModuleError on a broken module
