"""`Provider`'s own guarantees — the ones the rest of the codebase leans
on once the provider-name magic strings are routed through it (issue
#97). `GameStatus`/`PixelStage`/`SetupStep` need no equivalent: they are
plain `enum.Enum`, never compared against a bare string, and their values
are pinned by the migrations that created their native `sa.Enum`
columns."""

import pytest

from nani_pix_bot.models.enums import Provider
from nani_pix_bot.models.game import Game
from nani_pix_bot.services.search import anilist, shikimori, tenrai, tmdb


def test_provider_values_are_the_lowercase_routing_keys() -> None:
    # These exact strings are what callback data, `Game.source` and the
    # two screenshot columns have always carried — the enum replaces the
    # literals without changing a single stored or transmitted byte.
    assert {provider.value for provider in Provider} == {"anilist", "shikimori", "tenrai", "tmdb"}


def test_provider_is_a_str_so_a_bare_string_still_compares_equal() -> None:
    # Values read back out of the three String-backed columns are plain
    # `str`, never `Provider` instances (SQLAlchemy has no idea the
    # column is enum-shaped) — every comparison in the codebase relies on
    # this holding in both directions.
    read_back_from_a_string_column = "shikimori"
    assert read_back_from_a_string_column == Provider.SHIKIMORI
    # The membership form too: `game_service.screenshot_capable_providers` and
    # `_validated_provider` both test a bare string against a container
    # of members rather than against one member.
    assert read_back_from_a_string_column in (Provider.SHIKIMORI, Provider.TENRAI, Provider.TMDB)
    assert Provider(read_back_from_a_string_column) is Provider.SHIKIMORI
    assert isinstance(Provider.TMDB, str)


def test_provider_formats_as_its_value_not_its_member_name() -> None:
    # The reason this is `enum.StrEnum` rather than a hand-rolled
    # `(str, Enum)` mix: loguru builds nearly every log line in this
    # codebase by interpolation, and a mix whose `__str__` renders
    # "Provider.TENRAI" would corrupt those silently, with no error.
    assert str(Provider.TENRAI) == "tenrai"
    assert f"{Provider.TENRAI}" == "tenrai"
    assert f"{Provider.TENRAI!s}" == "tenrai"


def test_provider_display_names_are_the_brand_spellings() -> None:
    # The single source of truth for the capitalized vocabulary that used
    # to live in `_SERVICE_DISPLAY_NAMES`/`_SCREENSHOT_PROVIDER_LABELS`
    # and in each provider module's own inline literals.
    assert Provider.ANILIST.display_name == "AniList"
    assert Provider.SHIKIMORI.display_name == "Shikimori"
    assert Provider.TENRAI.display_name == "Tenrai"
    assert Provider.TMDB.display_name == "TMDB"


def test_every_provider_has_a_display_name() -> None:
    # `display_name`'s final branch is `assert_never`, so a fifth member
    # added without a label fails type-checking — this catches it at
    # runtime too, the same belt-and-braces `_current_setup_screen` uses.
    assert all(provider.display_name for provider in Provider)


def test_provider_pick_prefixes_are_the_wire_format_every_member_uses() -> None:
    # Replaces the old `_PICK_PREFIX_SOURCES` dict (issue #114) — every
    # member's prefix is exactly f"{value}_pick:", used verbatim by
    # keyboards.py's callback data and app.py's routing pattern.
    assert Provider.ANILIST.pick_prefix == "anilist_pick:"
    assert Provider.SHIKIMORI.pick_prefix == "shikimori_pick:"
    assert Provider.TENRAI.pick_prefix == "tenrai_pick:"
    assert Provider.TMDB.pick_prefix == "tmdb_pick:"


def test_provider_method_callback_data_is_the_method_picker_wire_format() -> None:
    # Single source of truth for the identification-method-picker's
    # per-provider callback data. keyboards.py's ANILIST_METHOD_CALLBACK_DATA
    # etc. stay named module constants (dm_start's tests import them
    # directly), but now derive from this property instead of an
    # independent f"method:{...}" restatement.
    assert Provider.ANILIST.method_callback_data == "method:anilist"
    assert Provider.SHIKIMORI.method_callback_data == "method:shikimori"
    assert Provider.TENRAI.method_callback_data == "method:tenrai"
    assert Provider.TMDB.method_callback_data == "method:tmdb"


def test_provider_id_attr_names_are_the_game_column_names() -> None:
    # Replaces the old `_ID_ATTRS` dict (issue #114).
    assert Provider.SHIKIMORI.id_attr_name == "shikimori_id"
    assert Provider.TENRAI.id_attr_name == "tenrai_id"
    assert Provider.TMDB.id_attr_name == "tmdb_id"


def test_provider_id_attr_names_are_real_attributes_on_game() -> None:
    # The reviewer's specific gap: `getattr(game, provider.id_attr_name)`
    # is typed `Any`, so a typo in one of the three column-name string
    # literals above would only surface if an integration test happened
    # to exercise that exact provider. This asserts the attribute genuinely
    # exists on a real `Game` instance for every screenshot-capable
    # provider, independent of any specific caller exercising it.
    game = Game()
    for provider in (Provider.SHIKIMORI, Provider.TENRAI, Provider.TMDB):
        assert hasattr(game, provider.id_attr_name)


def test_provider_id_attr_name_raises_for_anilist() -> None:
    # AniList identifies an anime but has no screenshot endpoint, so it has
    # no id column of its own — accessing this on Provider.ANILIST raises
    # rather than returning a nonexistent column name.
    with pytest.raises(ValueError, match=r"Provider\.ANILIST"):
        _ = Provider.ANILIST.id_attr_name


def test_provider_screenshot_module_resolves_to_the_real_search_module() -> None:
    # `is`, not `==`: each provider module's `service` adapter delegates
    # to that module's free functions by name, looked up at call time —
    # dm_start's tests patch those functions directly
    # (monkeypatch.setattr(shikimori, "screenshots", ...)), so this
    # property has to keep returning the same adapter singleton every
    # time, not an equal-but-different one (see services/search/base.py).
    assert Provider.SHIKIMORI.screenshot_module is shikimori.service
    assert Provider.TENRAI.screenshot_module is tenrai.service
    assert Provider.TMDB.screenshot_module is tmdb.service


def test_provider_screenshot_module_raises_for_anilist() -> None:
    # AniList has no screenshot endpoint — mirrors id_attr_name above.
    with pytest.raises(ValueError, match=r"Provider\.ANILIST"):
        _ = Provider.ANILIST.screenshot_module


def test_provider_search_module_covers_all_four_providers_including_anilist() -> None:
    # Deliberately not unified with screenshot_module: search_module covers
    # all four providers (AniList can be searched/identified even though it
    # has no screenshot endpoint), so unlike id_attr_name/screenshot_module
    # this must NOT raise for ANILIST.
    assert Provider.ANILIST.search_module is anilist.service
    assert Provider.SHIKIMORI.search_module is shikimori.service
    assert Provider.TENRAI.search_module is tenrai.service
    assert Provider.TMDB.search_module is tmdb.service
