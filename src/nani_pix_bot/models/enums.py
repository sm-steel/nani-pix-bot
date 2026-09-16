import enum
from types import ModuleType
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

    @property
    def pick_prefix(self) -> str:
        """The callback-data prefix an identification-search result's
        pick button carries for this provider, e.g. `"shikimori_pick:"`
        — see `keyboards.py`'s `parse_pick_callback_data`, which strips
        this prefix back off a tapped button's data to recover which
        provider it belongs to.

        Replaces the old `_PICK_PREFIX_SOURCES` dict (issue #114): the
        prefix was always exactly `f"{self}_pick:"` for every member, so
        keeping that as an external dict was one more restatement of a
        rule this enum already exists to hold in one place."""
        return f"{self}_pick:"

    @property
    def method_callback_data(self) -> str:
        """The callback data for this provider's button on the
        identification-method picker, e.g. `"method:shikimori"` — see
        `keyboards.py::method_selection_keyboard`.

        `keyboards.py`'s `ANILIST_METHOD_CALLBACK_DATA` etc. stay named
        module constants derived from this property, rather than moving
        onto `Provider` outright the way `pick_prefix`/`id_attr_name` did:
        several `dm_start` test files import those constants directly, and
        `MANUAL_METHOD_CALLBACK_DATA` has no `Provider` member to hang off
        of (see this enum's own docstring on `"manual"`) — so a plain
        f-string restatement here is what #114 was about eliminating, not
        the named constants themselves."""
        return f"method:{self}"

    @property
    def id_attr_name(self) -> str:
        """The `Game` column name that stores this provider's screenshot
        id — see `models/game.py`'s `shikimori_id`/`jikan_id`/`tmdb_id`
        columns.

        AniList identifies an anime but has no screenshot endpoint (see
        `screenshot_module` below) and so has no id column of its own —
        accessing this on `Provider.ANILIST` raises, the same way
        `screenshot_module` does. Every real call site only reaches this
        for a provider already known to be screenshot-capable (see
        `keyboards.py`'s `_SCREENSHOT_CAPABLE_PROVIDERS`), so the raise
        is defensive, not a path anything is expected to hit.

        Replaces the old `_ID_ATTRS` dict (issue #114)."""
        if self is Provider.SHIKIMORI:
            return "shikimori_id"
        if self is Provider.JIKAN:
            return "jikan_id"
        if self is Provider.TMDB:
            return "tmdb_id"
        raise ValueError(f"Provider.{self.name} has no screenshot id column")

    @property
    def screenshot_module(self) -> ModuleType:
        """The `services.search` module that can fetch this provider's
        screenshots — a **module** reference, not a bound function:
        commands/dm_start's tests patch
        `monkeypatch.setattr(shikimori, "screenshots", ...)` directly on
        the module object, which only keeps working if every caller
        looks the attribute up on the module at call time rather than
        capturing the function once.

        The import is lazy — inside this property's body, re-run on
        every access — deliberately not at module level and not behind
        `TYPE_CHECKING` (the module object is needed at runtime, not
        just for type-checking). `services/search/shikimori.py` (and
        `jikan.py`/`tmdb.py`) already import `Provider` from this module
        at their own top level, so a module-level import back here would
        be a real two-hop cycle (`models.enums` <-> `services.search.*`)
        — a lazy per-call import avoids it, since by the time this runs
        both modules have already finished importing once.

        AniList has no screenshot endpoint, so it isn't covered here —
        accessing this on `Provider.ANILIST` raises, mirroring
        `id_attr_name` above.

        Replaces the old `_SCREENSHOT_MODULES` dict (issue #114)."""
        from nani_pix_bot.services.search import jikan, shikimori, tmdb

        if self is Provider.SHIKIMORI:
            return shikimori
        if self is Provider.JIKAN:
            return jikan
        if self is Provider.TMDB:
            return tmdb
        raise ValueError(f"Provider.{self.name} has no screenshot module")

    @property
    def search_module(self) -> ModuleType:
        """The `services.search` module that can search/identify with
        this provider — all four members, including AniList (contrast
        `screenshot_module` above, which only covers the three that can
        also supply screenshots).

        Deliberately not unified with `screenshot_module`: widening the
        3-provider set to 4 would silently turn a stray AniList
        screenshot-lookup failure into a different kind of error
        elsewhere — an invisible-to-tests behavior change for no
        benefit. Same lazy-import reasoning as `screenshot_module` above
        — see its docstring for why the import lives inside this getter
        rather than at module level.

        Replaces the old `_SEARCH_MODULES` dict (issue #114)."""
        from nani_pix_bot.services.search import anilist, jikan, shikimori, tmdb

        if self is Provider.ANILIST:
            return anilist
        if self is Provider.SHIKIMORI:
            return shikimori
        if self is Provider.JIKAN:
            return jikan
        if self is Provider.TMDB:
            return tmdb
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


class PixelAlgorithm(enum.Enum):
    """How a pixelation block's single colour is chosen — see
    MECHANICS.md's "Pixelation stages" section. Orthogonal to
    `PixelStage`: the stage decides *how wide* the mosaic is, this
    decides *how each block is coloured*, and the starter picks one per
    game from the confirmation preview.

    The five survive a head-to-head comparison of nine candidates; the
    four that didn't were all whole-image blurs in some form, which
    obscure a 1280px screenshot far more than a 3840px one and stop
    being a mosaic at any useful strength. Implementations and the
    registry live in services/pixelate/."""

    # The original implementation, and every pre-existing game's look:
    # samples one arbitrary source pixel per block, so a lone bright
    # speck can define a whole block and fine texture aliases into
    # misleading patterns.
    NEAREST = "nearest"
    # The canonical mosaic — each block is the mean of its pixels.
    BOX = "box"
    # The default: keeps the block's dominant tone where averaging
    # muddies it toward grey.
    MEDIAN = "median"
    # Kept because it is cheap to offer and occasionally interesting on
    # flat cel-shaded art, but marked worst in the picker — see
    # services/pixelate's DISCOURAGED.
    MODE = "mode"
    # Sharpest block colours; can ring on high-contrast edges.
    LANCZOS = "lanczos"


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
