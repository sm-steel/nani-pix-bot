"""`Provider`'s own guarantees — the ones the rest of the codebase leans
on once the provider-name magic strings are routed through it (issue
#97). `GameStatus`/`PixelStage`/`SetupStep` need no equivalent: they are
plain `enum.Enum`, never compared against a bare string, and their values
are pinned by the migrations that created their native `sa.Enum`
columns."""

from nani_pix_bot.models.enums import Provider


def test_provider_values_are_the_lowercase_routing_keys() -> None:
    # These exact strings are what callback data, `Game.source` and the
    # two screenshot columns have always carried — the enum replaces the
    # literals without changing a single stored or transmitted byte.
    assert {provider.value for provider in Provider} == {"anilist", "shikimori", "jikan", "tmdb"}


def test_provider_is_a_str_so_a_bare_string_still_compares_equal() -> None:
    # Values read back out of the three String-backed columns are plain
    # `str`, never `Provider` instances (SQLAlchemy has no idea the
    # column is enum-shaped) — every comparison in the codebase relies on
    # this holding in both directions.
    read_back_from_a_string_column = "shikimori"
    assert read_back_from_a_string_column == Provider.SHIKIMORI
    # The membership form too: `_screenshot_capable_providers` and
    # `_validated_provider` both test a bare string against a container
    # of members rather than against one member.
    assert read_back_from_a_string_column in (Provider.SHIKIMORI, Provider.JIKAN, Provider.TMDB)
    assert Provider(read_back_from_a_string_column) is Provider.SHIKIMORI
    assert isinstance(Provider.TMDB, str)


def test_provider_formats_as_its_value_not_its_member_name() -> None:
    # The reason this is `enum.StrEnum` rather than a hand-rolled
    # `(str, Enum)` mix: loguru builds nearly every log line in this
    # codebase by interpolation, and a mix whose `__str__` renders
    # "Provider.JIKAN" would corrupt those silently, with no error.
    assert str(Provider.JIKAN) == "jikan"
    assert f"{Provider.JIKAN}" == "jikan"
    assert f"{Provider.JIKAN!s}" == "jikan"


def test_provider_display_names_are_the_brand_spellings() -> None:
    # The single source of truth for the capitalized vocabulary that used
    # to live in `_SERVICE_DISPLAY_NAMES`/`_SCREENSHOT_PROVIDER_LABELS`
    # and in each provider module's own inline literals.
    assert Provider.ANILIST.display_name == "AniList"
    assert Provider.SHIKIMORI.display_name == "Shikimori"
    assert Provider.JIKAN.display_name == "Jikan"
    assert Provider.TMDB.display_name == "TMDB"


def test_every_provider_has_a_display_name() -> None:
    # `display_name`'s final branch is `assert_never`, so a fifth member
    # added without a label fails type-checking — this catches it at
    # runtime too, the same belt-and-braces `_current_setup_screen` uses.
    assert all(provider.display_name for provider in Provider)
