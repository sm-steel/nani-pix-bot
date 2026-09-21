"""Tests for the one shared "is MAL linking configured?" predicate."""

import pytest

from nani_pix_bot.commands.helpers.mal_config import MAL_BOT_DATA_KEYS, mal_configured


def _bot_data(**overrides) -> dict:
    data = dict.fromkeys(MAL_BOT_DATA_KEYS, "set")
    data.update(overrides)
    return data


def test_all_four_set_is_configured() -> None:
    assert mal_configured(_bot_data()) is True


@pytest.mark.parametrize("missing_key", list(MAL_BOT_DATA_KEYS))
def test_any_one_missing_is_not_configured(missing_key: str) -> None:
    """All four or nothing. A partial set used to render the 6th method
    button and let /linkmal run, which could spend a player's single-use
    MAL authorization code on a link that could never be stored."""
    assert mal_configured(_bot_data(**{missing_key: None})) is False


def test_an_empty_string_counts_as_missing() -> None:
    assert mal_configured(_bot_data(mal_redirect_uri="")) is False


def test_an_empty_bot_data_is_not_configured() -> None:
    assert mal_configured({}) is False
