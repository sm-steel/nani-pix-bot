from nani_pix_bot.models.enums import CosmeticSlot
from nani_pix_bot.seasons import registry
from nani_pix_bot.services.cosmetics import looks


def _fake(monkeypatch):
    runs = registry.discover("tests.seasons.fake_runs")
    monkeypatch.setattr(registry, "all_runs", lambda: runs)
    registry.all_items.cache_clear()
    return runs


def test_items_get_namespaced_ids(monkeypatch) -> None:
    _fake(monkeypatch)
    resolved = registry.item("demo_1/name_flair.blossom")
    assert resolved is not None
    assert resolved.item.slot is CosmeticSlot.NAME_FLAIR
    assert resolved.run.run_id == "demo_1"
    assert registry.item("demo_1/nope") is None
    assert registry.item("garbage") is None


def test_every_shipped_item_resolves() -> None:
    registry.all_runs.cache_clear()
    registry.all_items.cache_clear()
    for item_id, resolved in registry.all_items().items():
        assert item_id == f"{resolved.run.run_id}/{resolved.item.key}"


def test_every_shipped_item_is_valid() -> None:
    registry.all_runs.cache_clear()
    registry.all_items.cache_clear()
    assert [p for r in registry.all_items().values() for p in looks.problems(r)] == []
