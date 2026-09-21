import pytest

from nani_pix_bot.commands.dm_start.keyboards import (
    ANILIST_METHOD_CALLBACK_DATA,
    MAL_METHOD_CALLBACK_DATA,
    MANUAL_METHOD_CALLBACK_DATA,
    PREVIEW_ADD_SYNONYM_CALLBACK_DATA,
    PREVIEW_CHANGE_IMAGE_CALLBACK_DATA,
    PREVIEW_CONFIRM_CALLBACK_DATA,
    PREVIEW_PIXEL_ALGORITHM_BACK_CALLBACK_DATA,
    PREVIEW_PIXEL_ALGORITHM_CALLBACK_DATA,
    PREVIEW_PIXEL_ALGORITHM_PICK_PREFIX,
    PREVIEW_RESEARCH_CALLBACK_DATA,
    SCREENSHOT_UPLOAD_CALLBACK_DATA,
    SEARCH_RETRY_CALLBACK_DATA,
    SHIKIMORI_METHOD_CALLBACK_DATA,
    TENRAI_METHOD_CALLBACK_DATA,
    TMDB_METHOD_CALLBACK_DATA,
    GalleryPage,
    MalListPage,
    anilist_results_keyboard,
    mal_list_keyboard,
    method_selection_keyboard,
    parse_mal_list_page_callback_data,
    parse_mal_list_pick_callback_data,
    parse_method_callback_data,
    parse_pick_callback_data,
    parse_screenshot_more_callback_data,
    parse_screenshot_pick_callback_data,
    parse_screenshot_search_again_callback_data,
    parse_screenshot_search_pick_callback_data,
    parse_screenshot_source_callback_data,
    pixel_algorithm_keyboard,
    preview_keyboard,
    screenshot_gallery_keyboard,
    screenshot_source_keyboard,
    shikimori_results_keyboard,
    tenrai_results_keyboard,
    tmdb_results_keyboard,
)
from nani_pix_bot.models.enums import DEFAULT_ALGORITHM, PixelAlgorithm, Provider
from nani_pix_bot.services.search.anilist import AniListResult
from nani_pix_bot.services.search.shikimori import ShikimoriResult
from nani_pix_bot.services.search.tenrai import TenraiResult
from nani_pix_bot.services.search.tmdb import TMDBResult

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

_FRIEREN_TENRAI = TenraiResult(
    tenrai_id=52991,
    title_romaji="Sousou no Frieren",
    title_english="Frieren: Beyond Journey's End",
    title_native="葬送のフリーレン",
    synonyms=[],
)
_FRIEREN_TMDB = TMDBResult(
    tmdb_id=209867,
    title_romaji=None,
    title_english="Frieren: Beyond Journey's End",
    title_native="葬送のフリーレン",
    synonyms=[],
)
_TMDB_NO_ENGLISH_TITLE = TMDBResult(
    tmdb_id=1, title_romaji=None, title_english=None, title_native="Some Anime", synonyms=[]
)

_TENRAI_NO_ENGLISH_TITLE = TenraiResult(
    tenrai_id=1, title_romaji="Some Anime", title_english=None, title_native=None, synonyms=[]
)


def test_keyboard_has_one_button_per_result_plus_a_retry_button() -> None:
    markup = anilist_results_keyboard([_FRIEREN, _NO_YEAR], lang="en")

    assert len(markup.inline_keyboard) == 3
    assert markup.inline_keyboard[-1][0].callback_data == SEARCH_RETRY_CALLBACK_DATA


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


def test_every_provider_has_a_pick_prefix_in_the_expected_wire_format() -> None:
    """Both halves of the pairing app.py's routing pattern depends on.

    app.py builds that pattern by interpolating every Provider member
    into "<provider>_pick:" rather than hand-listing four literals, and
    `parse_pick_callback_data` (via `Provider.pick_prefix`, issue #114)
    is what turns a matched prefix back into the member. Nothing else
    connects them: a prefix format changed here (or a fifth member added
    to only one side) leaves both files internally consistent and the
    buttons silently unroutable — which is the exact bug that shipped
    when tenrai/tmdb were added.

    test_app.py can't catch that on its own any more, because since #97
    both sides of its assertion derive from Provider. So the pairing is
    pinned here instead: `pick_prefix`'s wire format on every member, and
    that `parse_pick_callback_data` actually recovers each member from
    its own prefix."""
    assert {provider.pick_prefix for provider in Provider} == {
        f"{provider}_pick:" for provider in Provider
    }
    for provider in Provider:
        assert parse_pick_callback_data(f"{provider.pick_prefix}42") == (provider, 42)


def test_parse_pick_callback_data_returns_none_for_retry() -> None:
    assert parse_pick_callback_data(SEARCH_RETRY_CALLBACK_DATA) is None


def test_shikimori_keyboard_has_one_button_per_result_plus_a_retry_button() -> None:
    markup = shikimori_results_keyboard([_FRIEREN_SHIKIMORI, _NO_RUSSIAN_TITLE], lang="en")

    assert len(markup.inline_keyboard) == 3
    assert markup.inline_keyboard[-1][0].callback_data == SEARCH_RETRY_CALLBACK_DATA


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


def test_tenrai_keyboard_has_one_button_per_result_plus_a_retry_button() -> None:
    markup = tenrai_results_keyboard([_FRIEREN_TENRAI, _TENRAI_NO_ENGLISH_TITLE], lang="en")

    assert len(markup.inline_keyboard) == 3
    assert markup.inline_keyboard[-1][0].callback_data == SEARCH_RETRY_CALLBACK_DATA


def test_tenrai_keyboard_button_label_prefers_english_title() -> None:
    markup = tenrai_results_keyboard([_FRIEREN_TENRAI], lang="en")

    assert markup.inline_keyboard[0][0].text == "Frieren: Beyond Journey's End"


def test_tenrai_keyboard_button_label_falls_back_to_romaji() -> None:
    markup = tenrai_results_keyboard([_TENRAI_NO_ENGLISH_TITLE], lang="en")

    assert markup.inline_keyboard[0][0].text == "Some Anime"


def test_tenrai_pick_callback_data_round_trips_the_tenrai_id() -> None:
    markup = tenrai_results_keyboard([_FRIEREN_TENRAI], lang="en")
    data = markup.inline_keyboard[0][0].callback_data

    assert isinstance(data, str)
    assert parse_pick_callback_data(data) == ("tenrai", 52991)


def test_tmdb_keyboard_has_one_button_per_result_plus_a_retry_button() -> None:
    markup = tmdb_results_keyboard([_FRIEREN_TMDB, _TMDB_NO_ENGLISH_TITLE], lang="en")

    assert len(markup.inline_keyboard) == 3
    assert markup.inline_keyboard[-1][0].callback_data == SEARCH_RETRY_CALLBACK_DATA


def test_tmdb_keyboard_button_label_prefers_english_title() -> None:
    markup = tmdb_results_keyboard([_FRIEREN_TMDB], lang="en")

    assert markup.inline_keyboard[0][0].text == "Frieren: Beyond Journey's End"


def test_tmdb_keyboard_button_label_falls_back_to_native() -> None:
    markup = tmdb_results_keyboard([_TMDB_NO_ENGLISH_TITLE], lang="en")

    assert markup.inline_keyboard[0][0].text == "Some Anime"


def test_tmdb_pick_callback_data_round_trips_the_tmdb_id() -> None:
    markup = tmdb_results_keyboard([_FRIEREN_TMDB], lang="en")
    data = markup.inline_keyboard[0][0].callback_data

    assert isinstance(data, str)
    assert parse_pick_callback_data(data) == ("tmdb", 209867)


def test_shikimori_keyboard_accepts_a_custom_pick_prefix() -> None:
    """Ticket 8's cross-provider "Wrong anime? Search again" flow reuses
    this exact keyboard builder for its own results, routed through a
    different callback prefix so its picks land on a different handler
    than identification search's own pick_callback_handler."""
    markup = shikimori_results_keyboard(
        [_FRIEREN_SHIKIMORI], lang="en", pick_prefix="screenshot_search_pick:shikimori:"
    )

    assert markup.inline_keyboard[0][0].callback_data == "screenshot_search_pick:shikimori:52991"


@pytest.mark.parametrize(
    ("build", "results", "provider"),
    [
        pytest.param(shikimori_results_keyboard, [_FRIEREN_SHIKIMORI], "shikimori", id="shikimori"),
        pytest.param(tenrai_results_keyboard, [_FRIEREN_TENRAI], "tenrai", id="tenrai"),
        pytest.param(tmdb_results_keyboard, [_FRIEREN_TMDB], "tmdb", id="tmdb"),
    ],
)
def test_cross_search_keyboard_retries_into_the_screenshot_flow(build, results, provider) -> None:
    """ "None of these" on a screenshot cross-search must come back to the
    screenshot sub-flow, not to identification search's own retry — that
    handler edits the message to "type a new search query" with no
    keyboard at all, a buttonless prompt reached purely by accident."""
    markup = build(results, lang="en", pick_prefix=f"screenshot_search_pick:{provider}:")

    assert markup.inline_keyboard[-1][0].callback_data == f"screenshot_search_again:{provider}"


def test_identification_keyboard_still_retries_into_the_identification_flow() -> None:
    """The default routing is unchanged: a plain identification-search
    results keyboard keeps sending "None of these" to
    pick_callback_handler's own retry branch."""
    markup = shikimori_results_keyboard([_FRIEREN_SHIKIMORI], lang="en")

    assert markup.inline_keyboard[-1][0].callback_data == SEARCH_RETRY_CALLBACK_DATA


def test_tenrai_keyboard_accepts_a_custom_pick_prefix() -> None:
    markup = tenrai_results_keyboard(
        [_FRIEREN_TENRAI], lang="en", pick_prefix="screenshot_search_pick:tenrai:"
    )

    assert markup.inline_keyboard[0][0].callback_data == "screenshot_search_pick:tenrai:52991"


def test_tmdb_keyboard_accepts_a_custom_pick_prefix() -> None:
    markup = tmdb_results_keyboard(
        [_FRIEREN_TMDB], lang="en", pick_prefix="screenshot_search_pick:tmdb:"
    )

    assert markup.inline_keyboard[0][0].callback_data == "screenshot_search_pick:tmdb:209867"


def test_parse_screenshot_search_pick_callback_data_round_trips() -> None:
    assert parse_screenshot_search_pick_callback_data("screenshot_search_pick:tmdb:209867") == (
        "tmdb",
        209867,
    )


def test_parse_screenshot_search_pick_callback_data_returns_none_for_other_data() -> None:
    assert parse_screenshot_search_pick_callback_data("shikimori_pick:52991") is None


@pytest.mark.parametrize(
    ("parse", "data"),
    [
        pytest.param(parse_pick_callback_data, "anilist_pick:abc", id="pick-not-a-number"),
        pytest.param(parse_pick_callback_data, "shikimori_pick:", id="pick-empty-id"),
        pytest.param(parse_pick_callback_data, "tenrai_pick:-1", id="pick-negative-id"),
        pytest.param(parse_pick_callback_data, "tmdb_pick:1 OR 1", id="pick-injected-id"),
        # str.isdigit() is True for these but int() refuses them, so the
        # obvious guard would still have raised — see _validated_index.
        pytest.param(parse_pick_callback_data, "anilist_pick:²", id="pick-non-decimal-digit"),
        pytest.param(
            parse_screenshot_pick_callback_data,
            "screenshot_pick:shikimori:x",
            id="screenshot-pick-not-a-number",
        ),
        pytest.param(
            parse_screenshot_pick_callback_data,
            "screenshot_pick:bogus:1",
            id="screenshot-pick-unknown-provider",
        ),
        pytest.param(
            parse_screenshot_pick_callback_data,
            "screenshot_pick:anilist:0",
            id="screenshot-pick-provider-without-screenshots",
        ),
        pytest.param(
            parse_screenshot_pick_callback_data,
            "screenshot_pick:shikimori",
            id="screenshot-pick-no-index-segment",
        ),
        pytest.param(
            parse_screenshot_pick_callback_data,
            "screenshot_pick:tmdb:²",
            id="screenshot-pick-non-decimal-digit",
        ),
        pytest.param(
            parse_screenshot_more_callback_data,
            "screenshot_more:bogus:1",
            id="more-unknown-provider",
        ),
        pytest.param(
            parse_screenshot_more_callback_data, "screenshot_more:tmdb:x", id="more-not-a-number"
        ),
        pytest.param(
            parse_screenshot_more_callback_data, "screenshot_more:tmdb", id="more-no-offset-segment"
        ),
        pytest.param(
            parse_screenshot_more_callback_data,
            "screenshot_more:tmdb:²",
            id="more-non-decimal-digit",
        ),
        pytest.param(
            parse_screenshot_source_callback_data,
            "screenshot_source:evil",
            id="source-unknown-provider",
        ),
        pytest.param(
            parse_screenshot_source_callback_data, "screenshot_source:", id="source-empty-provider"
        ),
        pytest.param(
            parse_screenshot_search_pick_callback_data,
            "screenshot_search_pick:bogus:1",
            id="search-pick-unknown-provider",
        ),
        pytest.param(
            parse_screenshot_search_pick_callback_data,
            "screenshot_search_pick:tmdb:abc",
            id="search-pick-not-a-number",
        ),
        pytest.param(
            parse_screenshot_search_pick_callback_data,
            "screenshot_search_pick:tmdb:²",
            id="search-pick-non-decimal-digit",
        ),
        pytest.param(
            parse_screenshot_search_again_callback_data,
            "screenshot_search_again:evil",
            id="search-again-unknown-provider",
        ),
    ],
)
def test_parsers_reject_malformed_callback_payloads(parse, data: str) -> None:
    """Every CallbackQueryHandler in app.py matches on prefix only, so
    everything after that prefix is whatever the client chose to send —
    an MTProto client can put arbitrary `data` on a tap. A payload that
    carries a real prefix but a junk provider or index must come back as
    None (the already-handled "not a pick" path) rather than raising
    ValueError here, or later against `Provider.id_attr_name` /
    `Provider.screenshot_module`."""
    assert parse(data) is None


@pytest.mark.parametrize(
    ("parse", "data", "expected"),
    [
        pytest.param(parse_pick_callback_data, "anilist_pick:99", ("anilist", 99), id="pick"),
        pytest.param(
            parse_screenshot_pick_callback_data,
            "screenshot_pick:shikimori:5",
            ("shikimori", 5),
            id="screenshot-pick",
        ),
        pytest.param(
            parse_screenshot_more_callback_data,
            "screenshot_more:tenrai:10",
            ("tenrai", 10),
            id="more",
        ),
        pytest.param(
            parse_screenshot_source_callback_data, "screenshot_source:tmdb", "tmdb", id="source"
        ),
        pytest.param(
            parse_screenshot_search_pick_callback_data,
            "screenshot_search_pick:tmdb:209867",
            ("tmdb", 209867),
            id="search-pick",
        ),
        pytest.param(
            parse_screenshot_search_again_callback_data,
            "screenshot_search_again:tmdb",
            "tmdb",
            id="search-again",
        ),
    ],
)
def test_parsers_still_accept_well_formed_callback_payloads(parse, data: str, expected) -> None:
    assert parse(data) == expected


@pytest.mark.parametrize(
    ("parsed_provider", "expected"),
    [
        pytest.param(parse_pick_callback_data("anilist_pick:99"), Provider.ANILIST, id="pick"),
        pytest.param(
            parse_screenshot_pick_callback_data("screenshot_pick:shikimori:5"),
            Provider.SHIKIMORI,
            id="screenshot-pick",
        ),
        pytest.param(
            parse_screenshot_more_callback_data("screenshot_more:tenrai:10"),
            Provider.TENRAI,
            id="more",
        ),
        pytest.param(
            parse_screenshot_source_callback_data("screenshot_source:tmdb"),
            Provider.TMDB,
            id="source",
        ),
        pytest.param(
            parse_screenshot_search_pick_callback_data("screenshot_search_pick:tmdb:209867"),
            Provider.TMDB,
            id="search-pick",
        ),
        pytest.param(
            parse_screenshot_search_again_callback_data("screenshot_search_again:tmdb"),
            Provider.TMDB,
            id="search-again",
        ),
        pytest.param(
            parse_method_callback_data(TMDB_METHOD_CALLBACK_DATA), Provider.TMDB, id="method"
        ),
    ],
)
def test_parsers_hand_back_real_provider_members(parsed_provider, expected: Provider) -> None:
    """The one place client-forged callback data becomes a typed value.

    `==` would pass against a bare string too (Provider is a StrEnum), so
    these assert identity: the provider segment has to *become* a
    Provider here rather than travel on as an unchecked string into a
    `getattr`/dict lookup several frames later — which is how an unknown
    provider used to reach a KeyError, in one case after it had already
    been written to `screenshot_picker_provider`."""
    provider = parsed_provider[0] if isinstance(parsed_provider, tuple) else parsed_provider
    assert provider is expected


def test_parse_method_callback_data_keeps_manual_a_bare_string() -> None:
    """ "manual" names the absence of an automatic provider, so it is
    deliberately outside Provider — and must stay outside it here, where
    search.py's `if source == "manual":` branch reads it."""
    assert parse_method_callback_data(MANUAL_METHOD_CALLBACK_DATA) == "manual"
    assert not isinstance(parse_method_callback_data(MANUAL_METHOD_CALLBACK_DATA), Provider)


def test_method_selection_keyboard_defaults_to_anilist_first() -> None:
    markup = method_selection_keyboard(prefer_shikimori=False, lang="en", mal_configured=False)

    callbacks = [button.callback_data for row in markup.inline_keyboard for button in row]
    assert callbacks == [
        ANILIST_METHOD_CALLBACK_DATA,
        SHIKIMORI_METHOD_CALLBACK_DATA,
        TENRAI_METHOD_CALLBACK_DATA,
        TMDB_METHOD_CALLBACK_DATA,
        MANUAL_METHOD_CALLBACK_DATA,
    ]


def test_method_selection_keyboard_prefers_shikimori_first_when_asked() -> None:
    markup = method_selection_keyboard(prefer_shikimori=True, lang="en", mal_configured=False)

    callbacks = [button.callback_data for row in markup.inline_keyboard for button in row]
    assert callbacks == [
        SHIKIMORI_METHOD_CALLBACK_DATA,
        ANILIST_METHOD_CALLBACK_DATA,
        TENRAI_METHOD_CALLBACK_DATA,
        TMDB_METHOD_CALLBACK_DATA,
        MANUAL_METHOD_CALLBACK_DATA,
    ]


def test_method_selection_keyboard_brand_name_labels_are_untranslated() -> None:
    # Intentional exception — these are third-party brand names, not
    # translated UI text, so they stay the same regardless of lang.
    markup_en = method_selection_keyboard(prefer_shikimori=False, lang="en", mal_configured=False)
    markup_ru = method_selection_keyboard(prefer_shikimori=False, lang="ru", mal_configured=False)

    labels_en = [button.text for row in markup_en.inline_keyboard for button in row]
    labels_ru = [button.text for row in markup_ru.inline_keyboard for button in row]
    assert labels_en[0] == "AniList"
    assert labels_en[1] == "Shikimori"
    assert labels_en[2] == "Tenrai"
    assert labels_en[3] == "TMDB"
    assert labels_ru[0] == "AniList"
    assert labels_ru[1] == "Shikimori"
    assert labels_ru[2] == "Tenrai"
    assert labels_ru[3] == "TMDB"


def test_method_selection_keyboard_manual_entry_label_is_translated() -> None:
    markup = method_selection_keyboard(prefer_shikimori=False, lang="ru", mal_configured=False)

    manual_label = markup.inline_keyboard[4][0].text
    assert "вручную" in manual_label.lower()


def test_method_selection_keyboard_includes_mal_button_when_configured() -> None:
    markup = method_selection_keyboard(prefer_shikimori=False, lang="en", mal_configured=True)

    callbacks = [button.callback_data for row in markup.inline_keyboard for button in row]
    assert callbacks == [
        ANILIST_METHOD_CALLBACK_DATA,
        SHIKIMORI_METHOD_CALLBACK_DATA,
        TENRAI_METHOD_CALLBACK_DATA,
        TMDB_METHOD_CALLBACK_DATA,
        MANUAL_METHOD_CALLBACK_DATA,
        MAL_METHOD_CALLBACK_DATA,
    ]


def test_method_selection_keyboard_omits_mal_button_when_not_configured() -> None:
    markup = method_selection_keyboard(prefer_shikimori=False, lang="en", mal_configured=False)

    callbacks = [button.callback_data for row in markup.inline_keyboard for button in row]
    assert MAL_METHOD_CALLBACK_DATA not in callbacks


def test_parse_method_callback_data_recognizes_mal_list() -> None:
    assert parse_method_callback_data(MAL_METHOD_CALLBACK_DATA) == "mal_list"


def test_parse_method_callback_data_round_trips() -> None:
    assert parse_method_callback_data(ANILIST_METHOD_CALLBACK_DATA) == "anilist"
    assert parse_method_callback_data(SHIKIMORI_METHOD_CALLBACK_DATA) == "shikimori"
    assert parse_method_callback_data(TENRAI_METHOD_CALLBACK_DATA) == "tenrai"
    assert parse_method_callback_data(TMDB_METHOD_CALLBACK_DATA) == "tmdb"
    assert parse_method_callback_data(MANUAL_METHOD_CALLBACK_DATA) == "manual"
    assert parse_method_callback_data(SEARCH_RETRY_CALLBACK_DATA) is None


def test_preview_keyboard_has_the_five_expected_buttons() -> None:
    markup = preview_keyboard(lang="en", algorithm=DEFAULT_ALGORITHM)

    callbacks = [button.callback_data for row in markup.inline_keyboard for button in row]
    assert callbacks == [
        PREVIEW_CONFIRM_CALLBACK_DATA,
        PREVIEW_CHANGE_IMAGE_CALLBACK_DATA,
        PREVIEW_RESEARCH_CALLBACK_DATA,
        PREVIEW_ADD_SYNONYM_CALLBACK_DATA,
        PREVIEW_PIXEL_ALGORITHM_CALLBACK_DATA,
    ]


def test_preview_keyboard_names_the_games_current_algorithm() -> None:
    """The label is the only place the current choice is visible without
    opening the submenu."""
    default = preview_keyboard(lang="en", algorithm=DEFAULT_ALGORITHM)
    lanczos = preview_keyboard(lang="en", algorithm=PixelAlgorithm.LANCZOS)

    assert "Median" in default.inline_keyboard[-1][0].text
    assert "Lanczos" in lanczos.inline_keyboard[-1][0].text


def test_pixel_algorithm_keyboard_offers_every_algorithm_plus_back() -> None:
    markup = pixel_algorithm_keyboard(lang="en", current=DEFAULT_ALGORITHM)

    callbacks = [button.callback_data for row in markup.inline_keyboard for button in row]
    assert callbacks == [
        *(f"{PREVIEW_PIXEL_ALGORITHM_PICK_PREFIX}{a.value}" for a in PixelAlgorithm),
        PREVIEW_PIXEL_ALGORITHM_BACK_CALLBACK_DATA,
    ]


def test_pixel_algorithm_keyboard_marks_the_current_and_the_worst() -> None:
    markup = pixel_algorithm_keyboard(lang="en", current=PixelAlgorithm.BOX)

    labels = {
        str(button.callback_data).removeprefix(PREVIEW_PIXEL_ALGORITHM_PICK_PREFIX): button.text
        for row in markup.inline_keyboard
        for button in row
        if str(button.callback_data).startswith(PREVIEW_PIXEL_ALGORITHM_PICK_PREFIX)
    }
    assert "✅" in labels["box"]
    assert "⚠️" in labels["mode"]
    assert "✅" not in labels["mode"]
    assert "⚠️" not in labels["box"]


def test_pixel_algorithm_keyboard_can_mark_one_button_both_ways() -> None:
    """The discouraged algorithm is still selectable, so it can be the
    current one — and must then carry both markers rather than losing
    the warning."""
    markup = pixel_algorithm_keyboard(lang="en", current=PixelAlgorithm.MODE)

    mode = next(
        button.text
        for row in markup.inline_keyboard
        for button in row
        if button.callback_data == f"{PREVIEW_PIXEL_ALGORITHM_PICK_PREFIX}mode"
    )
    assert "✅" in mode
    assert "⚠️" in mode


def test_pixel_algorithm_keyboard_labels_are_translated() -> None:
    markup_en = pixel_algorithm_keyboard(lang="en", current=DEFAULT_ALGORITHM)
    markup_ru = pixel_algorithm_keyboard(lang="ru", current=DEFAULT_ALGORITHM)

    labels_en = [button.text for row in markup_en.inline_keyboard for button in row]
    labels_ru = [button.text for row in markup_ru.inline_keyboard for button in row]
    assert labels_en != labels_ru
    assert any("Median" in label for label in labels_en)
    assert any("Медиана" in label for label in labels_ru)


def test_preview_keyboard_labels_are_translated() -> None:
    markup_en = preview_keyboard(lang="en", algorithm=DEFAULT_ALGORITHM)
    markup_ru = preview_keyboard(lang="ru", algorithm=DEFAULT_ALGORITHM)

    labels_en = [button.text for row in markup_en.inline_keyboard for button in row]
    labels_ru = [button.text for row in markup_ru.inline_keyboard for button in row]
    assert labels_en != labels_ru
    assert "Confirm" in labels_en[0]
    assert "Подтвердить" in labels_ru[0] or "подтвердить" in labels_ru[0].lower()


def _source_rows(markup):
    return [(button.text, button.callback_data) for row in markup.inline_keyboard for button in row]


def test_screenshot_source_keyboard_marks_the_provider_that_just_failed() -> None:
    """A failed provider stays tappable — a 504 is often transient, and
    it may be the only source with screenshots for this title — but it's
    flagged so it isn't retried by accident."""
    markup = screenshot_source_keyboard(
        [Provider.SHIKIMORI, Provider.TENRAI, Provider.TMDB], "en", failed_provider=Provider.TENRAI
    )

    labels = dict(_source_rows(markup))
    tenrai_label = next(text for text in labels if "Tenrai" in text)
    assert tenrai_label.startswith("⚠️")
    assert labels[tenrai_label] == "screenshot_source:tenrai"
    # Only that one is marked.
    assert not any(text.startswith("⚠️") for text in labels if "Tenrai" not in text)


def test_screenshot_source_keyboard_marks_nothing_by_default() -> None:
    markup = screenshot_source_keyboard([Provider.SHIKIMORI, Provider.TENRAI, Provider.TMDB], "en")

    assert not any(text.startswith("⚠️") for text, _ in _source_rows(markup))


def test_screenshot_source_keyboard_always_offers_upload_instead() -> None:
    """The upload escape has to survive on the failure screen too."""
    markup = screenshot_source_keyboard([Provider.TENRAI], "en", failed_provider=Provider.TENRAI)

    callbacks = [data for _, data in _source_rows(markup)]
    assert SCREENSHOT_UPLOAD_CALLBACK_DATA in callbacks


def _gallery_rows(markup) -> list[list[tuple[str, str]]]:
    return [[(b.text, b.callback_data) for b in row] for row in markup.inline_keyboard]


def test_screenshot_gallery_keyboard_numbers_match_the_album_captions() -> None:
    """The album photos are captioned with absolute positions, so the
    pick buttons under them must be too — page 2 labelled 1,2,3 under
    photos captioned 6,7,8 told the starter the wrong thing (the
    callback data was right all along)."""
    page = GalleryPage(
        provider=Provider.SHIKIMORI,
        offset=5,
        count=3,
        has_more=False,
        cross_provider=False,
        previous_offset=0,
    )

    numbers = _gallery_rows(screenshot_gallery_keyboard(page, "en"))[0]
    assert [text for text, _ in numbers] == ["6", "7", "8"]
    assert [data for _, data in numbers] == [
        "screenshot_pick:shikimori:5",
        "screenshot_pick:shikimori:6",
        "screenshot_pick:shikimori:7",
    ]


def test_screenshot_gallery_keyboard_offers_back_past_the_first_page() -> None:
    page = GalleryPage(
        provider=Provider.SHIKIMORI,
        offset=5,
        count=5,
        has_more=True,
        cross_provider=False,
        previous_offset=0,
    )

    callbacks = [
        data for row in _gallery_rows(screenshot_gallery_keyboard(page, "en")) for _, data in row
    ]
    assert "screenshot_more:shikimori:0" in callbacks  # back
    assert "screenshot_more:shikimori:10" in callbacks  # forward


def test_screenshot_gallery_keyboard_has_no_back_button_on_the_first_page() -> None:
    page = GalleryPage(
        provider=Provider.SHIKIMORI,
        offset=0,
        count=5,
        has_more=True,
        cross_provider=False,
        previous_offset=None,
    )

    callbacks = [
        data for row in _gallery_rows(screenshot_gallery_keyboard(page, "en")) for _, data in row
    ]
    assert "screenshot_more:shikimori:5" in callbacks  # forward only
    assert not any(data.endswith(":0") for data in callbacks if data.startswith("screenshot_more:"))


def test_screenshot_gallery_keyboard_puts_both_paging_buttons_on_one_row() -> None:
    page = GalleryPage(
        provider=Provider.SHIKIMORI,
        offset=5,
        count=5,
        has_more=True,
        cross_provider=False,
        previous_offset=0,
    )

    paging = [
        row
        for row in _gallery_rows(screenshot_gallery_keyboard(page, "en"))
        if all(data.startswith("screenshot_more:") for _, data in row)
    ]
    assert len(paging) == 1
    assert len(paging[0]) == 2


def test_mal_list_keyboard_includes_one_button_per_entry_with_status_tag() -> None:
    page = MalListPage(
        offset=0,
        count=2,
        has_more=True,
        previous_offset=None,
        entries=[
            (52991, "Frieren: Beyond Journey's End", "completed"),
            (1, "Cowboy Bebop", "plan_to_watch"),
        ],
    )

    labels = [text for row in _gallery_rows(mal_list_keyboard(page, "en")) for text, _ in row]
    assert any("Frieren" in label and "Completed" in label for label in labels)
    assert any("Cowboy Bebop" in label and "Plan to Watch" in label for label in labels)


def test_mal_list_keyboard_next_button_present_when_has_more() -> None:
    page = MalListPage(
        offset=0, count=1, has_more=True, previous_offset=None, entries=[(1, "X", "completed")]
    )

    callbacks = [data for row in _gallery_rows(mal_list_keyboard(page, "en")) for _, data in row]
    next_offset = parse_mal_list_page_callback_data(
        next(cd for cd in callbacks if cd.startswith("mal_list_page:"))
    )
    assert next_offset == 1  # offset + count


def test_mal_list_keyboard_previous_button_absent_on_first_page() -> None:
    page = MalListPage(
        offset=0, count=1, has_more=True, previous_offset=None, entries=[(1, "X", "completed")]
    )

    callbacks = [data for row in _gallery_rows(mal_list_keyboard(page, "en")) for _, data in row]
    page_offsets = [
        parse_mal_list_page_callback_data(cd) for cd in callbacks if cd.startswith("mal_list_page:")
    ]
    assert 0 not in page_offsets  # no "go to offset 0" button when already there


def test_mal_list_keyboard_previous_button_present_past_the_first_page() -> None:
    page = MalListPage(
        offset=1, count=1, has_more=False, previous_offset=0, entries=[(1, "X", "completed")]
    )

    callbacks = [data for row in _gallery_rows(mal_list_keyboard(page, "en")) for _, data in row]
    assert "mal_list_page:0" in callbacks


def test_mal_list_keyboard_paging_buttons_share_one_row() -> None:
    page = MalListPage(
        offset=1, count=1, has_more=True, previous_offset=0, entries=[(1, "X", "completed")]
    )

    paging = [
        row
        for row in _gallery_rows(mal_list_keyboard(page, "en"))
        if all(data.startswith("mal_list_page:") for _, data in row)
    ]
    assert len(paging) == 1
    assert len(paging[0]) == 2


def test_parse_mal_list_pick_callback_data_extracts_mal_id() -> None:
    page = MalListPage(
        offset=0,
        count=1,
        has_more=False,
        previous_offset=None,
        entries=[(52991, "Frieren", "completed")],
    )

    callbacks = [data for row in _gallery_rows(mal_list_keyboard(page, "en")) for _, data in row]
    pick_data = next(cd for cd in callbacks if cd.startswith("mal_list_pick:"))
    assert parse_mal_list_pick_callback_data(pick_data) == 52991


def test_parse_mal_list_page_callback_data_returns_none_for_garbage() -> None:
    assert parse_mal_list_page_callback_data("not-a-real-prefix:5") is None


def test_parse_mal_list_pick_callback_data_returns_none_for_garbage() -> None:
    assert parse_mal_list_pick_callback_data("not-a-real-prefix:5") is None


def test_mal_status_labels_are_translated_to_russian() -> None:
    page = MalListPage(
        offset=0, count=1, has_more=False, previous_offset=None, entries=[(1, "X", "watching")]
    )

    label = _gallery_rows(mal_list_keyboard(page, "ru"))[0][0][0]
    assert "Смотрю" in label


def test_mal_status_label_falls_back_to_raw_status_when_unrecognized() -> None:
    page = MalListPage(
        offset=0, count=1, has_more=False, previous_offset=None, entries=[(1, "X", "unknown")]
    )

    label = _gallery_rows(mal_list_keyboard(page, "en"))[0][0][0]
    assert label == "[unknown] X"
