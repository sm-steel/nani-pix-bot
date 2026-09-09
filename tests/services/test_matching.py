from nani_pix_bot.services.matching import is_match, normalize

_TITLES = ["Sousou no Frieren", "Frieren: Beyond Journey's End"]
_SYNONYMS = ["Frieren", "Frieren at the Funeral"]


def test_normalize_lowercases_strips_punctuation_and_collapses_whitespace() -> None:
    assert normalize("  Frieren:  Beyond Journey's End!!  ") == "frieren beyond journeys end"


def test_is_match_accepts_the_exact_title() -> None:
    assert is_match("Sousou no Frieren", _TITLES + _SYNONYMS)


def test_is_match_accepts_a_known_synonym() -> None:
    assert is_match("frieren", _TITLES + _SYNONYMS)


def test_is_match_accepts_a_close_typo() -> None:
    assert is_match("friren", _TITLES + _SYNONYMS)


def test_is_match_rejects_unrelated_text() -> None:
    assert not is_match("attack on titan", _TITLES + _SYNONYMS)


def test_is_match_rejects_an_empty_guess() -> None:
    assert not is_match("   ", _TITLES + _SYNONYMS)


def test_is_match_ignores_blank_candidates() -> None:
    assert not is_match("frieren", ["", None, ""])  # type: ignore[list-item]
