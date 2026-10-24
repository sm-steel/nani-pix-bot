from dataclasses import replace

from nani_pix_bot.models.enums import CosmeticSlot, GameStatus, ItemSource
from nani_pix_bot.seasons import registry
from nani_pix_bot.seasons.definition import Item
from nani_pix_bot.services import mascot
from nani_pix_bot.services.cosmetics import costume, inventory

STARTER = 2


def _runs(monkeypatch, look: str, run_default: str | None = None):
    demo = registry.discover("tests.seasons.fake_runs")["demo_1"]
    hat = Item(
        "mascot_costume.hat", CosmeticSlot.MASCOT_COSTUME, look, {"EN": "Hat", "RU": "Шляпа"}
    )
    run = replace(demo, items=(*demo.items, hat), mascot_costume=run_default)
    monkeypatch.setattr(registry, "all_runs", lambda: {"demo_1": run})
    registry.all_items.cache_clear()
    return run


def _wear(session, item_id: str = "demo_1/mascot_costume.hat") -> None:
    inventory.grant(session, STARTER, inventory.ItemGrant(item_id, ItemSource.ADMIN))
    inventory.equip(session, STARTER, item_id)


def test_the_starters_equipped_costume_wins(session, tagged_game, costume_art, monkeypatch) -> None:
    _runs(monkeypatch, costume_art, run_default="pumpkin")
    _wear(session)
    assert costume.for_game(session, tagged_game) == "test_hat"


def test_no_equipped_costume_uses_the_games_run_default(
    session, tagged_game, costume_art, monkeypatch
) -> None:
    _runs(monkeypatch, costume_art, run_default="test_hat")
    assert costume.for_game(session, tagged_game) == "test_hat"


def test_a_bot_started_game_uses_the_run_default(
    session, bot_tagged_game, costume_art, monkeypatch
) -> None:
    _runs(monkeypatch, costume_art, run_default="pumpkin")
    assert costume.for_game(session, bot_tagged_game) == "pumpkin"


def test_a_game_from_before_the_season_stays_plain(
    session, untagged_game, active_season, costume_art, monkeypatch
) -> None:
    _runs(monkeypatch, costume_art, run_default="pumpkin")
    assert untagged_game.status is GameStatus.ACTIVE
    assert costume.for_game(session, untagged_game) is None


def test_a_setup_game_uses_the_active_run(
    session, untagged_game, active_season, costume_art, monkeypatch
) -> None:
    _runs(monkeypatch, costume_art, run_default="pumpkin")
    untagged_game.status = GameStatus.SETUP
    assert costume.for_game(session, untagged_game) == "pumpkin"


def test_an_equipped_costume_from_an_older_run_still_applies(
    session, tagged_game, costume_art, monkeypatch
) -> None:
    run = _runs(monkeypatch, costume_art)
    _wear(session)
    # the item's run is no longer the one the game is tagged to
    other = replace(run, run_id="other_1", items=())
    monkeypatch.setattr(registry, "all_runs", lambda: {"demo_1": run, "other_1": other})
    tagged_game.season_id = None
    assert costume.for_game(session, tagged_game) == "test_hat"


def test_an_unresolvable_equipped_costume_falls_to_the_run_default(
    session, tagged_game, costume_art, monkeypatch
) -> None:
    _runs(monkeypatch, costume_art, run_default="pumpkin")
    _wear(session)
    for pose in (mascot.COSTUME_DIR / costume_art).iterdir():
        pose.unlink()
    assert costume.for_game(session, tagged_game) == "pumpkin"


def test_for_game_id_of_a_deleted_game_is_none(session) -> None:
    assert costume.for_game_id(session, 999) is None


def test_for_game_id_finds_the_game(session, bot_tagged_game, costume_art, monkeypatch) -> None:
    _runs(monkeypatch, costume_art, run_default="pumpkin")
    assert costume.for_game_id(session, bot_tagged_game.id) == "pumpkin"


def test_for_viewer_uses_the_active_run(session, active_season, costume_art, monkeypatch) -> None:
    _runs(monkeypatch, costume_art, run_default="pumpkin")
    assert costume.for_viewer(session, STARTER) == "pumpkin"
    _wear(session)
    assert costume.for_viewer(session, STARTER) == "test_hat"


def test_no_run_and_nothing_equipped_is_plain(session) -> None:
    assert costume.for_viewer(session, STARTER) is None
    assert costume.for_player(session, None, None) is None


def test_a_run_default_with_missing_art_is_a_problem() -> None:
    demo = registry.discover("tests.seasons.fake_runs")["demo_1"]
    found = costume.problems(replace(demo, mascot_costume="nope_hat"))
    assert found
    assert all(p.startswith("mascot_costume: ") for p in found)


def test_a_run_without_a_costume_has_no_problems() -> None:
    demo = registry.discover("tests.seasons.fake_runs")["demo_1"]
    assert costume.problems(demo) == []


def test_every_shipped_run_default_is_valid() -> None:
    runs = registry.discover(registry.RUNS_PACKAGE)
    assert [p for r in runs.values() for p in costume.problems(r)] == []
