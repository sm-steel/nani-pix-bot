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
