import re

import pytest

from nani_pix_bot.services import i18n


def _fake_load(catalogs: dict[str, dict[str, str]]):
    def loader(lang: str) -> dict[str, str]:
        return catalogs.get(lang, {})

    return loader


def test_t_formats_the_template(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(i18n, "_load", _fake_load({"en": {"greet": "Hello {name}"}}))

    assert i18n.t("greet", "en", name="Aleksey") == "Hello Aleksey"


def test_t_is_case_insensitive_on_language(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(i18n, "_load", _fake_load({"en": {"greet": "Hi"}}))

    assert i18n.t("greet", "EN") == "Hi"


def test_t_falls_back_to_english_when_key_missing_in_other_language(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(i18n, "_load", _fake_load({"en": {"greet": "Hi"}, "ru": {}}))

    assert i18n.t("greet", "ru") == "Hi"


def test_t_returns_the_key_itself_when_missing_even_in_english(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(i18n, "_load", _fake_load({"en": {}}))

    assert i18n.t("mystery.key", "en") == "mystery.key"


def test_real_locale_files_have_matching_keys() -> None:
    """Guards against B's per-ticket string additions drifting between
    languages — every key in one locale must exist in the other."""
    en_keys = set(i18n._load("en"))
    ru_keys = set(i18n._load("ru"))

    assert en_keys == ru_keys


def _placeholders(template: str) -> set[str]:
    return set(re.findall(r"\{(\w+)\}", template))


def test_real_locale_files_have_matching_placeholders_per_key() -> None:
    """Matching key sets are only half of parity: `t()` formats whatever
    the caller passes, so a Russian string that spells a placeholder
    differently (or drops one) silently loses the value — or, if it
    invents one, raises `KeyError` at the moment a player sees it. The
    key set is checked above; this pins the templates themselves."""
    en = i18n._load("en")
    ru = i18n._load("ru")

    mismatched = {
        key: (_placeholders(en[key]), _placeholders(value))
        for key, value in ru.items()
        if key in en and _placeholders(en[key]) != _placeholders(value)
    }

    assert mismatched == {}


def _fake_load_variations(catalogs: dict[str, dict[str, list[str]]]):
    def loader(lang: str) -> dict[str, list[str]]:
        return catalogs.get(lang, {})

    return loader


def test_t_can_pick_the_canonical_template_from_the_pool(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(i18n, "_load", _fake_load({"en": {"greet": "Hello {name}"}}))
    monkeypatch.setattr(
        i18n, "_load_variations", _fake_load_variations({"en": {"greet": ["Yo {name}"]}})
    )
    monkeypatch.setattr(i18n.secrets, "choice", lambda pool: pool[0])

    assert i18n.t("greet", "en", name="Aleksey") == "Hello Aleksey"


def test_t_can_pick_a_variation_from_the_pool(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(i18n, "_load", _fake_load({"en": {"greet": "Hello {name}"}}))
    monkeypatch.setattr(
        i18n, "_load_variations", _fake_load_variations({"en": {"greet": ["Yo {name}"]}})
    )
    monkeypatch.setattr(i18n.secrets, "choice", lambda pool: pool[-1])

    assert i18n.t("greet", "en", name="Aleksey") == "Yo Aleksey"


def test_t_without_a_variations_entry_behaves_as_before(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(i18n, "_load", _fake_load({"en": {"greet": "Hello {name}"}}))
    monkeypatch.setattr(i18n, "_load_variations", _fake_load_variations({"en": {}}))

    assert i18n.t("greet", "en", name="Aleksey") == "Hello Aleksey"


def test_t_with_no_variations_file_for_the_language_degrades_to_canonical(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(i18n, "_load", _fake_load({"en": {"greet": "Hello {name}"}}))
    monkeypatch.setattr(i18n, "_load_variations", _fake_load_variations({}))

    assert i18n.t("greet", "en", name="Aleksey") == "Hello Aleksey"


def test_t_uses_variations_from_the_resolved_language_after_fallback(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """ru is missing the key, so t() falls back to en — the variation pool
    must come from en too, not from ru's (unrelated) variations for the
    same key, or a Russian-facing player would silently see English flavor
    text spliced onto an otherwise-English fallback for no good reason."""
    monkeypatch.setattr(i18n, "_load", _fake_load({"en": {"greet": "Hi"}, "ru": {}}))
    monkeypatch.setattr(
        i18n,
        "_load_variations",
        _fake_load_variations(
            {"en": {"greet": ["Hi there"]}, "ru": {"greet": ["should not be used"]}}
        ),
    )
    monkeypatch.setattr(i18n.secrets, "choice", lambda pool: pool[-1])

    assert i18n.t("greet", "ru") == "Hi there"


def test_the_turn_and_no_game_prompts_name_both_entry_points() -> None:
    """There are two ways to start a round — DM a screenshot, or
    /newgame — and these five strings are the moments a player is told
    how. They documented only the upload for the whole /newgame range
    (issue #79). This repo's known failure mode is a mechanics change
    that sweeps the docs and forgets `locales/`, so the guard is a test,
    not a habit."""
    both_entry_points = [
        "dm_start.setup_abandoned",
        "guess.no_game",
        "turn.reminder_dm",
        "turn.reminder_group_fallback",
        "turn.expired",
    ]

    for lang in ("en", "ru"):
        catalog = i18n._load(lang)
        for key in both_entry_points:
            assert "/newgame" in catalog[key], f"{key} ({lang}) never mentions /newgame"
