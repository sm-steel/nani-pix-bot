from nani_pix_bot.commands.helpers.keyboards import (
    ANILIST_METHOD_CALLBACK_DATA,
    MANUAL_METHOD_CALLBACK_DATA,
    PREVIEW_ADD_SYNONYM_CALLBACK_DATA,
    PREVIEW_CHANGE_IMAGE_CALLBACK_DATA,
    PREVIEW_CONFIRM_CALLBACK_DATA,
    PREVIEW_RESEARCH_CALLBACK_DATA,
    RETRY_CALLBACK_DATA,
    SHIKIMORI_METHOD_CALLBACK_DATA,
    STOP_CANCEL_CALLBACK_DATA,
    STOP_CONFIRM_CALLBACK_DATA,
    anilist_results_keyboard,
    method_selection_keyboard,
    parse_method_callback_data,
    parse_pick_callback_data,
    preview_keyboard,
    shikimori_results_keyboard,
    stop_confirm_keyboard,
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
_FRIEREN_SHIKIMORI_ALL_TITLES = ShikimoriResult(
    shikimori_id=52991,
    title_romaji="Sousou no Frieren",
    title_english="Frieren: Beyond Journey's End",
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
    markup = anilist_results_keyboard([_FRIEREN, _NO_YEAR], lang="en")

    assert len(markup.inline_keyboard) == 3
    assert markup.inline_keyboard[-1][0].callback_data == RETRY_CALLBACK_DATA


def test_keyboard_button_label_prefers_english_title_and_shows_year() -> None:
    markup = anilist_results_keyboard([_FRIEREN], lang="en")

    assert markup.inline_keyboard[0][0].text == "Frieren: Beyond Journey's End (2023)"


def test_keyboard_button_label_falls_back_to_romaji_with_no_year() -> None:
    markup = anilist_results_keyboard([_NO_YEAR], lang="en")

    assert markup.inline_keyboard[0][0].text == "Some Anime"


def test_keyboard_button_label_prefers_english_regardless_of_lang() -> None:
    # AniList has no Russian-specific title field, so lang doesn't
    # currently change which title wins here — unlike Shikimori's.
    markup_en = anilist_results_keyboard([_FRIEREN], lang="en")
    markup_ru = anilist_results_keyboard([_FRIEREN], lang="ru")

    assert markup_en.inline_keyboard[0][0].text == markup_ru.inline_keyboard[0][0].text


def test_retry_button_label_is_translated() -> None:
    markup = anilist_results_keyboard([_NO_YEAR], lang="ru")

    assert "искать" in markup.inline_keyboard[-1][0].text.lower()


def test_pick_callback_data_round_trips_the_anilist_id() -> None:
    markup = anilist_results_keyboard([_FRIEREN], lang="en")
    data = markup.inline_keyboard[0][0].callback_data

    assert isinstance(data, str)
    assert parse_pick_callback_data(data) == ("anilist", 99)


def test_parse_pick_callback_data_returns_none_for_retry() -> None:
    assert parse_pick_callback_data(RETRY_CALLBACK_DATA) is None


def test_shikimori_keyboard_has_one_button_per_result_plus_a_retry_button() -> None:
    markup = shikimori_results_keyboard([_FRIEREN_SHIKIMORI, _NO_RUSSIAN_TITLE], lang="en")

    assert len(markup.inline_keyboard) == 3
    assert markup.inline_keyboard[-1][0].callback_data == RETRY_CALLBACK_DATA


def test_shikimori_keyboard_button_label_prefers_the_russian_title_when_lang_is_ru() -> None:
    markup = shikimori_results_keyboard([_FRIEREN_SHIKIMORI_ALL_TITLES], lang="ru")

    assert markup.inline_keyboard[0][0].text == "Провожающая в последний путь Фрирен"


def test_shikimori_keyboard_button_label_prefers_english_when_lang_is_en() -> None:
    markup = shikimori_results_keyboard([_FRIEREN_SHIKIMORI_ALL_TITLES], lang="en")

    assert markup.inline_keyboard[0][0].text == "Frieren: Beyond Journey's End"


def test_shikimori_keyboard_button_label_prefers_romaji_over_russian_when_lang_is_en() -> None:
    markup = shikimori_results_keyboard([_FRIEREN_SHIKIMORI], lang="en")

    assert markup.inline_keyboard[0][0].text == "Sousou no Frieren"


def test_shikimori_keyboard_button_label_falls_back_to_russian_as_last_resort() -> None:
    only_russian = ShikimoriResult(
        shikimori_id=1, title_romaji=None, title_english=None, title_russian="Фрирен", synonyms=[]
    )
    markup = shikimori_results_keyboard([only_russian], lang="en")

    assert markup.inline_keyboard[0][0].text == "Фрирен"


def test_shikimori_keyboard_button_label_falls_back_to_romaji() -> None:
    markup = shikimori_results_keyboard([_NO_RUSSIAN_TITLE], lang="en")

    assert markup.inline_keyboard[0][0].text == "Some Anime"


def test_shikimori_pick_callback_data_round_trips_the_shikimori_id() -> None:
    markup = shikimori_results_keyboard([_FRIEREN_SHIKIMORI], lang="en")
    data = markup.inline_keyboard[0][0].callback_data

    assert isinstance(data, str)
    assert parse_pick_callback_data(data) == ("shikimori", 52991)


def test_method_selection_keyboard_defaults_to_anilist_first() -> None:
    markup = method_selection_keyboard(prefer_shikimori=False, lang="en")

    callbacks = [button.callback_data for row in markup.inline_keyboard for button in row]
    assert callbacks == [
        ANILIST_METHOD_CALLBACK_DATA,
        SHIKIMORI_METHOD_CALLBACK_DATA,
        MANUAL_METHOD_CALLBACK_DATA,
    ]


def test_method_selection_keyboard_prefers_shikimori_first_when_asked() -> None:
    markup = method_selection_keyboard(prefer_shikimori=True, lang="en")

    callbacks = [button.callback_data for row in markup.inline_keyboard for button in row]
    assert callbacks == [
        SHIKIMORI_METHOD_CALLBACK_DATA,
        ANILIST_METHOD_CALLBACK_DATA,
        MANUAL_METHOD_CALLBACK_DATA,
    ]


def test_method_selection_keyboard_anilist_and_shikimori_labels_are_brand_names() -> None:
    # Intentional exception — these are third-party brand names, not
    # translated UI text, so they stay the same regardless of lang.
    markup_en = method_selection_keyboard(prefer_shikimori=False, lang="en")
    markup_ru = method_selection_keyboard(prefer_shikimori=False, lang="ru")

    labels_en = [button.text for row in markup_en.inline_keyboard for button in row]
    labels_ru = [button.text for row in markup_ru.inline_keyboard for button in row]
    assert labels_en[0] == "AniList"
    assert labels_en[1] == "Shikimori"
    assert labels_ru[0] == "AniList"
    assert labels_ru[1] == "Shikimori"


def test_method_selection_keyboard_manual_entry_label_is_translated() -> None:
    markup = method_selection_keyboard(prefer_shikimori=False, lang="ru")

    manual_label = markup.inline_keyboard[2][0].text
    assert "вручную" in manual_label.lower()


def test_parse_method_callback_data_round_trips() -> None:
    assert parse_method_callback_data(ANILIST_METHOD_CALLBACK_DATA) == "anilist"
    assert parse_method_callback_data(SHIKIMORI_METHOD_CALLBACK_DATA) == "shikimori"
    assert parse_method_callback_data(MANUAL_METHOD_CALLBACK_DATA) == "manual"
    assert parse_method_callback_data(RETRY_CALLBACK_DATA) is None


def test_preview_keyboard_has_the_four_expected_buttons() -> None:
    markup = preview_keyboard(lang="en")

    callbacks = [button.callback_data for row in markup.inline_keyboard for button in row]
    assert callbacks == [
        PREVIEW_CONFIRM_CALLBACK_DATA,
        PREVIEW_CHANGE_IMAGE_CALLBACK_DATA,
        PREVIEW_RESEARCH_CALLBACK_DATA,
        PREVIEW_ADD_SYNONYM_CALLBACK_DATA,
    ]


def test_preview_keyboard_labels_are_translated() -> None:
    markup_en = preview_keyboard(lang="en")
    markup_ru = preview_keyboard(lang="ru")

    labels_en = [button.text for row in markup_en.inline_keyboard for button in row]
    labels_ru = [button.text for row in markup_ru.inline_keyboard for button in row]
    assert labels_en != labels_ru
    assert "Confirm" in labels_en[0]
    assert "Подтвердить" in labels_ru[0] or "подтвердить" in labels_ru[0].lower()


def test_stop_confirm_keyboard_has_confirm_and_cancel_buttons() -> None:
    markup = stop_confirm_keyboard(lang="en")

    callbacks = [button.callback_data for row in markup.inline_keyboard for button in row]
    assert callbacks == [STOP_CONFIRM_CALLBACK_DATA, STOP_CANCEL_CALLBACK_DATA]


def test_stop_confirm_keyboard_labels_are_translated() -> None:
    markup_en = stop_confirm_keyboard(lang="en")
    markup_ru = stop_confirm_keyboard(lang="ru")

    labels_en = [button.text for row in markup_en.inline_keyboard for button in row]
    labels_ru = [button.text for row in markup_ru.inline_keyboard for button in row]
    assert labels_en != labels_ru
