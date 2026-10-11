import json
from pathlib import Path

import pytest

from nani_pix_bot.models.enums import EventType, Rarity
from nani_pix_bot.services.achievements import catalogue
from nani_pix_bot.services.achievements.definitions import Kind

LOCALES = Path(__file__).resolve().parents[3] / "src" / "nani_pix_bot" / "locales"


def test_catalogue_keys_are_unique_and_complete() -> None:
    keys = [d.key for d in catalogue.CATALOGUE]
    assert len(keys) == len(set(keys)) == 52


def test_definitions_contain_every_catalogue_entry() -> None:
    assert catalogue.CATALOGUE
    assert {d.key for d in catalogue.CATALOGUE} <= {d.key for d in catalogue.definitions()}


def test_every_definition_key_is_unique() -> None:
    keys = [d.key for d in catalogue.definitions()]
    assert len(keys) == len(set(keys))


def test_every_entry_has_a_name_and_description_in_both_languages() -> None:
    for lang in ("en", "ru"):
        strings = json.loads((LOCALES / f"{lang}.json").read_text(encoding="utf-8"))
        for defn in catalogue.definitions():
            assert f"achievement.{defn.key}.name" in strings, (lang, defn.key)
            assert f"achievement.{defn.key}.desc" in strings, (lang, defn.key)


def test_exactly_three_hidden_entries() -> None:
    hidden = {d.key for d in catalogue.definitions() if d.hidden}
    assert hidden == {"dethroned", "so_close", "penny_pincher"}


def test_triggered_by_finds_definitions_by_event_type() -> None:
    keys = {d.key for d in catalogue.triggered_by(EventType.GAME_WON)}
    assert {"sharpshooter", "pioneer", "milestone_keeper", "clutch"} <= keys
    assert catalogue.triggered_by(EventType.STAGE_ADVANCED) == ()


def test_period_definitions_have_no_triggers() -> None:
    for key in ("champion_week", "champion_month", "champion_year"):
        defn = catalogue.get(key)
        assert defn.kind is Kind.PERIOD
        assert defn.triggers == frozenset()


def test_a_clue_share_triggers_no_achievement() -> None:
    assert catalogue.triggered_by(EventType.CLUE_SHARED) == ()


def test_get_raises_for_an_unknown_key() -> None:
    with pytest.raises(KeyError):
        catalogue.get("no_such_achievement")


def test_season_achievement_rarities_are_the_approved_ones() -> None:
    r = Rarity
    expected = {
        "season_debut": r.BRONZE,
        "season_opening_act": r.SILVER,
        "season_curtain_call": r.SILVER,
        "season_pass_complete": r.GOLD,
        "season_pass_premium": r.PLATINUM,
        "season_window_shopper": r.BRONZE,
        "season_collector": r.GOLD,
        "season_podium_3": r.SILVER,
        "season_podium_2": r.GOLD,
        "season_podium_1": r.PLATINUM,
        "season_on_theme": r.SILVER,
    }
    got = {d.key: d.rarities[0] for d in catalogue.CATALOGUE if d.kind is Kind.SEASON}
    assert got == expected
