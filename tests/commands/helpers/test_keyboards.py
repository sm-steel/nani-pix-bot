from nani_pix_bot.commands.helpers.keyboards import (
    ANILIST_METHOD_CALLBACK_DATA,
    MANUAL_METHOD_CALLBACK_DATA,
    PREVIEW_ADD_SYNONYM_CALLBACK_DATA,
    PREVIEW_CHANGE_IMAGE_CALLBACK_DATA,
    PREVIEW_CONFIRM_CALLBACK_DATA,
    PREVIEW_RESEARCH_CALLBACK_DATA,
    RETRY_CALLBACK_DATA,
    SHIKIMORI_METHOD_CALLBACK_DATA,
    anilist_results_keyboard,
    method_selection_keyboard,
    parse_method_callback_data,
    parse_pick_callback_data,
    preview_keyboard,
    shikimori_results_keyboard,
)
from nani_pix_bot.services.anilist import AniListResult
from nani_pix_bot.services.shikimori import ShikimoriResult

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

_FRIEREN_SHIKIMORI = ShikimoriResult(
    shikimori_id=52991,
    title_romaji="Sousou no Frieren",
    title_english=None,
    title_russian="Провожающая в последний путь Фрирен",
    synonyms=[],
)
_NO_RUSSIAN_TITLE = ShikimoriResult(
    shikimori_id=1,
    title_romaji="Some Anime",
    title_english=None,
    title_russian=None,
    synonyms=[],
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
    assert parse_pick_callback_data(data) == ("anilist", 99)


def test_parse_pick_callback_data_returns_none_for_retry() -> None:
    assert parse_pick_callback_data(RETRY_CALLBACK_DATA) is None


def test_shikimori_keyboard_has_one_button_per_result_plus_a_retry_button() -> None:
    markup = shikimori_results_keyboard([_FRIEREN_SHIKIMORI, _NO_RUSSIAN_TITLE])

    assert len(markup.inline_keyboard) == 3
    assert markup.inline_keyboard[-1][0].callback_data == RETRY_CALLBACK_DATA


def test_shikimori_keyboard_button_label_prefers_the_russian_title() -> None:
    markup = shikimori_results_keyboard([_FRIEREN_SHIKIMORI])

    assert markup.inline_keyboard[0][0].text == "Провожающая в последний путь Фрирен"


def test_shikimori_keyboard_button_label_falls_back_to_romaji() -> None:
    markup = shikimori_results_keyboard([_NO_RUSSIAN_TITLE])

    assert markup.inline_keyboard[0][0].text == "Some Anime"


def test_shikimori_pick_callback_data_round_trips_the_shikimori_id() -> None:
    markup = shikimori_results_keyboard([_FRIEREN_SHIKIMORI])
    data = markup.inline_keyboard[0][0].callback_data

    assert isinstance(data, str)
    assert parse_pick_callback_data(data) == ("shikimori", 52991)


def test_method_selection_keyboard_defaults_to_anilist_first() -> None:
    markup = method_selection_keyboard(prefer_shikimori=False)

    callbacks = [button.callback_data for row in markup.inline_keyboard for button in row]
    assert callbacks == [
        ANILIST_METHOD_CALLBACK_DATA,
        SHIKIMORI_METHOD_CALLBACK_DATA,
        MANUAL_METHOD_CALLBACK_DATA,
    ]


def test_method_selection_keyboard_prefers_shikimori_first_when_asked() -> None:
    markup = method_selection_keyboard(prefer_shikimori=True)

    callbacks = [button.callback_data for row in markup.inline_keyboard for button in row]
    assert callbacks == [
        SHIKIMORI_METHOD_CALLBACK_DATA,
        ANILIST_METHOD_CALLBACK_DATA,
        MANUAL_METHOD_CALLBACK_DATA,
    ]


def test_parse_method_callback_data_round_trips() -> None:
    assert parse_method_callback_data(ANILIST_METHOD_CALLBACK_DATA) == "anilist"
    assert parse_method_callback_data(SHIKIMORI_METHOD_CALLBACK_DATA) == "shikimori"
    assert parse_method_callback_data(MANUAL_METHOD_CALLBACK_DATA) == "manual"
    assert parse_method_callback_data(RETRY_CALLBACK_DATA) is None


def test_preview_keyboard_has_the_four_expected_buttons() -> None:
    markup = preview_keyboard()

    callbacks = [button.callback_data for row in markup.inline_keyboard for button in row]
    assert callbacks == [
        PREVIEW_CONFIRM_CALLBACK_DATA,
        PREVIEW_CHANGE_IMAGE_CALLBACK_DATA,
        PREVIEW_RESEARCH_CALLBACK_DATA,
        PREVIEW_ADD_SYNONYM_CALLBACK_DATA,
    ]
