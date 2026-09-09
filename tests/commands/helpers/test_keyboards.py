from nani_pix_bot.commands.helpers.keyboards import (
    RETRY_CALLBACK_DATA,
    anilist_results_keyboard,
    parse_pick_callback_data,
)
from nani_pix_bot.services.anilist import AniListResult

_FRIEREN = AniListResult(
    anilist_id=99,
    title_romaji="Sousou no Frieren",
    title_english="Frieren: Beyond Journey's End",
    title_native="葬送のフリーレン",
    synonyms=["Frieren"],
    year=2023,
)
_NO_YEAR = AniListResult(
    anilist_id=1,
    title_romaji="Some Anime",
    title_english=None,
    title_native=None,
    synonyms=[],
    year=None,
)


def test_keyboard_has_one_button_per_result_plus_a_retry_button() -> None:
    markup = anilist_results_keyboard([_FRIEREN, _NO_YEAR])

    assert len(markup.inline_keyboard) == 3
    assert markup.inline_keyboard[-1][0].callback_data == RETRY_CALLBACK_DATA


def test_keyboard_button_label_prefers_english_title_and_shows_year() -> None:
    markup = anilist_results_keyboard([_FRIEREN])

    assert markup.inline_keyboard[0][0].text == "Frieren: Beyond Journey's End (2023)"


def test_keyboard_button_label_falls_back_to_romaji_with_no_year() -> None:
    markup = anilist_results_keyboard([_NO_YEAR])

    assert markup.inline_keyboard[0][0].text == "Some Anime"


def test_pick_callback_data_round_trips_the_anilist_id() -> None:
    markup = anilist_results_keyboard([_FRIEREN])
    data = markup.inline_keyboard[0][0].callback_data

    assert isinstance(data, str)
    assert parse_pick_callback_data(data) == 99


def test_parse_pick_callback_data_returns_none_for_retry() -> None:
    assert parse_pick_callback_data(RETRY_CALLBACK_DATA) is None
