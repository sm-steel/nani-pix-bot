import enum
from typing import assert_never


class Provider(enum.StrEnum):
    """Which external service a game's identification, screenshot or
    picker state refers to — the single source of truth for both halves
    of the vocabulary that used to be six hand-maintained dicts and a
    routing regex (issue #97): the lowercase routing key (callback data,
    the three `Game` columns) *is* the member's value, and the
    capitalized brand spelling is `display_name` below.

    **The only `str`-mixin enum in this module, deliberately.**
    `GameStatus`/`PixelStage`/`SetupStep` are plain `enum.Enum` and get
    native `sa.Enum` columns, created by their own migrations, which
    store each member's *name*. These four values are already in the
    database as lowercase strings in plain `String` columns, and this
    refactor ships no migration — so `Provider` has to compare and
    persist as its value, in both directions, for every existing row to
    keep meaning what it means. See `models/game.py`'s three columns,
    which must keep their explicit `String(...)` argument for exactly
    that reason.

    `enum.StrEnum` (stdlib since 3.11) rather than a hand-rolled
    `class Provider(str, enum.Enum)`: the latter's `__str__`/`__format__`
    can render `"Provider.JIKAN"` instead of `"jikan"` depending on the
    Python version, and nearly every log line in this codebase is built
    by loguru interpolation — that failure mode would corrupt log output
    with no error anywhere.

    **`"manual"` is not a member.** It is the fifth legal value of
    `Game.source`, but it means "no automatic provider" — there is no
    service behind it to search, fetch screenshots from, or name in a
    message — so it lives in the `Literal["manual"]` half of that
    column's union instead, and only appears in the
    identification-method-selection context."""

    ANILIST = "anilist"
    SHIKIMORI = "shikimori"
    JIKAN = "jikan"
    TMDB = "tmdb"

    @property
    def display_name(self) -> str:
        """The brand spelling shown to a starter — button labels, and the
        `{service}` placeholder in every provider-naming i18n string.
        Deliberately untranslated in both languages (see CLAUDE.md's i18n
        section): these are third-party brand names, not UI text.

        A property with an exhaustive if/elif rather than a dict, for the
        same reason this enum exists: a same-shaped external dict would
        just be one more restatement of the mapping. The `assert_never`
        tail matches `_shared.py::_current_setup_screen`'s precedent — a
        fifth member added without a label fails type-checking rather
        than silently falling through to someone else's name."""
        if self is Provider.ANILIST:
            return "AniList"
        if self is Provider.SHIKIMORI:
            return "Shikimori"
        if self is Provider.JIKAN:
            return "Jikan"
        if self is Provider.TMDB:
            return "TMDB"
        assert_never(self)


class GameStatus(enum.Enum):
    """A Game's lifecycle — see MECHANICS.md's "Game lifecycle" section
    for the full state diagram."""

    SETUP = "setup"  # starter is picking/confirming the anime in DM
    ACTIVE = "active"  # posted to the group, guessing open
    WON = "won"  # a /guess matched, or /correct forced it (terminal)
    UNSOLVED = "unsolved"  # stage exhaustion or 2-day timeout (terminal)


class PixelStage(enum.Enum):
    """Pixelation stages, blockiest to clearest — see MECHANICS.md's
    "Pixelation stages" table. Named ordinally (not by a scale factor —
    that was tried before and the digits stopped meaning anything real).
    Only the 5 members and their order are fixed here; each stage's
    actual target width and wrong-guess limit are admin-configurable at
    runtime, not baked into this enum — see services/settings/stage_config.py."""

    STAGE_1 = "stage_1"  # shown first — blockiest/hardest
    STAGE_2 = "stage_2"
    STAGE_3 = "stage_3"
    STAGE_4 = "stage_4"
    STAGE_5 = "stage_5"  # clearest stage before reveal


class SetupStep(enum.Enum):
    """Where a SETUP game's starter currently is in the multi-step DM
    identification flow — see MECHANICS.md's "Starting a game" section.
    Only meaningful while `Game.status` is `SETUP`; derived from the DB,
    never cached in user_data (issue #11)."""

    # Covers everything up to and including picking/typing a candidate
    # title (method choice, search, result pick, manual title+synonym) —
    # the finer-grained state within this step is read off Game.source
    # and title_english, not a separate enum value.
    PICKING_METHOD = "picking_method"  # method choice, search, or manual title/synonym entry
    # Entered once identification is staged but there's still no image
    # (the screenshot-less /newgame path) — covers both the
    # screenshot-source-selection buttons and browsing the resulting
    # gallery ("More screenshots" included). Exited either by picking a
    # screenshot (-> CONFIRMING) or tapping "Upload my own instead"
    # (-> AWAITING_PHOTO_CHANGE, same as the traditional flow's own
    # "Change image" button).
    PICKING_SCREENSHOT = "picking_screenshot"
    AWAITING_PHOTO_CHANGE = "awaiting_photo_change"  # preview's "Change image" tapped
    AWAITING_SYNONYM = "awaiting_synonym"  # preview's "Add a synonym" tapped
    CONFIRMING = "confirming"  # showing the preview, waiting for a button tap
