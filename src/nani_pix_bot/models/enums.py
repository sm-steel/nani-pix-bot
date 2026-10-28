import enum
from typing import TYPE_CHECKING, assert_never

if TYPE_CHECKING:
    from nani_pix_bot.services.search.base import ScreenshotModule, SearchModule


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
    can render `"Provider.TENRAI"` instead of `"tenrai"` depending on the
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
    TENRAI = "tenrai"
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
        tail means a fifth member added without a label fails
        type-checking rather than silently falling through to someone
        else's name."""
        if self is Provider.ANILIST:
            return "AniList"
        if self is Provider.SHIKIMORI:
            return "Shikimori"
        if self is Provider.TENRAI:
            return "Tenrai"
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
        id — see `models/game.py`'s `shikimori_id`/`tenrai_id`/`tmdb_id`
        columns.

        AniList identifies an anime but has no screenshot endpoint (see
        `screenshot_module` below) and so has no id column of its own —
        accessing this on `Provider.ANILIST` raises, the same way
        `screenshot_module` does. Every real call site only reaches this
        for a provider already known to be screenshot-capable (see
        `services/game/state.py`'s `SCREENSHOT_CAPABLE_PROVIDERS`), so the raise
        is defensive, not a path anything is expected to hit.

        Replaces the old `_ID_ATTRS` dict (issue #114)."""
        if self is Provider.SHIKIMORI:
            return "shikimori_id"
        if self is Provider.TENRAI:
            return "tenrai_id"
        if self is Provider.TMDB:
            return "tmdb_id"
        raise ValueError(f"Provider.{self.name} has no screenshot id column")

    @property
    def screenshot_module(self) -> "ScreenshotModule":
        """The `services.search.base.ScreenshotModule` adapter that can
        fetch this provider's screenshots — e.g. `shikimori.py`'s
        `service` singleton, not the `shikimori` module itself. Every
        adapter method delegates to that same module's free functions by
        an unqualified name lookup resolved at call time, so
        commands/dm_start's tests patching
        `monkeypatch.setattr(shikimori, "screenshots", ...)` directly on
        the module still take effect here — see `services/search/base.py`'s
        module docstring for the full reasoning (issue #169), including
        why this moved off bare `types.ModuleType`.

        The import is lazy — inside this property's body, re-run on
        every access — deliberately not at module level and not behind
        `TYPE_CHECKING` (the module object is needed at runtime, not
        just for type-checking). `services/search/shikimori.py` (and
        `tenrai.py`/`tmdb.py`) already import `Provider` from this module
        at their own top level, so a module-level import back here would
        be a real two-hop cycle (`models.enums` <-> `services.search.*`)
        — a lazy per-call import avoids it, since by the time this runs
        both modules have already finished importing once.

        AniList has no screenshot endpoint, so it isn't covered here —
        accessing this on `Provider.ANILIST` raises, mirroring
        `id_attr_name` above.

        Replaces the old `_SCREENSHOT_MODULES` dict (issue #114)."""
        from nani_pix_bot.services.search import shikimori, tenrai, tmdb

        if self is Provider.SHIKIMORI:
            return shikimori.service
        if self is Provider.TENRAI:
            return tenrai.service
        if self is Provider.TMDB:
            return tmdb.service
        raise ValueError(f"Provider.{self.name} has no screenshot module")

    @property
    def search_module(self) -> "SearchModule":
        """The `services.search.base.SearchModule` adapter that can
        search/identify with this provider — all four members, including
        AniList (contrast `screenshot_module` above, which only covers
        the three that can also supply screenshots). See
        `screenshot_module`'s docstring above for what these adapters
        actually are and why this moved off bare `types.ModuleType`
        (issue #169).

        Deliberately not unified with `screenshot_module`: widening the
        3-provider set to 4 would silently turn a stray AniList
        screenshot-lookup failure into a different kind of error
        elsewhere — an invisible-to-tests behavior change for no
        benefit. Same lazy-import reasoning as `screenshot_module` above
        — see its docstring for why the import lives inside this getter
        rather than at module level.

        Replaces the old `_SEARCH_MODULES` dict (issue #114)."""
        from nani_pix_bot.services.search import anilist, shikimori, tenrai, tmdb

        if self is Provider.ANILIST:
            return anilist.service
        if self is Provider.SHIKIMORI:
            return shikimori.service
        if self is Provider.TENRAI:
            return tenrai.service
        if self is Provider.TMDB:
            return tmdb.service
        assert_never(self)


class GameStatus(enum.Enum):
    """A Game's lifecycle — see MECHANICS.md's "Game lifecycle" section
    for the full state diagram."""

    SETUP = "setup"  # starter is picking/confirming the anime in DM
    ACTIVE = "active"  # posted to the group, guessing open
    WON = "won"  # a /guess matched, or /correct forced it (terminal)
    UNSOLVED = "unsolved"  # stage exhaustion or 2-day timeout (terminal)
    VOTING = "voting"  # HARD MODE ended with no correct guess; the group votes (issue #252)


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


# What a game gets unless its starter picks otherwise. MEDIAN rather than
# the original NEAREST because NEAREST samples one arbitrary pixel per
# block and visibly speckles on real screenshots, where a rank-based
# mosaic holds the block's dominant tone.
DEFAULT_ALGORITHM = PixelAlgorithm.MEDIAN

# Offered, but flagged as the worst choice in the starter's picker.
# ModeFilter falls back to the centre pixel whenever no colour repeats in
# its window; on a real screenshot (gradient-heavy, softened by
# compression) that is most windows, so it speckles harder than the
# NEAREST it was supposed to improve on.
DISCOURAGED_ALGORITHMS = frozenset({PixelAlgorithm.MODE})

# Both constants live here rather than in services/pixelate because
# models/game.py needs the default for its column and models must not
# depend on services — services/pixelate re-exports them so callers still
# have a single import site.


class RevealEffect(enum.Enum):
    """Which transition the animated end-of-game reveal (issue #295) uses
    to go from the pixelated image to the clear one. Persisted by name in
    `reveal_video.effect`, like the other enums."""

    IRIS = "iris"
    TILE_FLIP = "tile_flip"
    RIPPLE = "ripple"
    GLITCH = "glitch"
    SHATTER = "shatter"
    # Sakura Season transitions: only ever an equipped cosmetic, never a random pick
    # (services/reveal/store.py's RANDOM_EFFECTS).
    SPRING_BREEZE = "spring_breeze"
    BLOSSOM_BRANCH = "blossom_branch"
    # Yōkai Night transitions: only ever an equipped cosmetic, never a random pick
    # (excluded from RANDOM_EFFECTS in services/reveal/store.py).
    CANDLE_SNUFF = "candle_snuff"
    NIGHT_PARADE = "night_parade"
    GASHADOKURO_RISE = "gashadokuro_rise"


class RevealStatus(enum.Enum):
    """Where the pre-rendered reveal video is: still rendering, ready to
    send, or the render failed. Persisted by name in
    `reveal_video.status`, like the other enums."""

    PENDING = "pending"
    READY = "ready"
    FAILED = "failed"


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
    # Right before the preview, for a number-heavy title ("91 Days"): the
    # bot asks whether its numbers matter (issue #345). Exited by either
    # answer, straight to CONFIRMING.
    ASKING_NUMBERS = "asking_numbers"
    # Right before the preview (and the numbers question), when the
    # background search found more names on other providers: the creator
    # ticks which to accept too (issue #348). Exited by "Add" or "Skip".
    PICKING_ALIASES = "picking_aliases"
    CONFIRMING = "confirming"  # showing the preview, waiting for a button tap
    # The clue panel's "Upload my own" was tapped (issue #452): the next DM
    # photo joins the author's clue screenshot list, never the main image.
    # Exited back to CONFIRMING by that photo.
    AWAITING_CLUE_PHOTO = "awaiting_clue_photo"


class ClueScreenshotMode(enum.StrEnum):
    """Where a game's "Extra screenshot" clues come from, chosen by its
    starter on the setup preview's clue panel (issue #450). Stored as a
    plain string, like `Provider`. Games the bot starts itself (autostart,
    HARD MODE) have no author and stay RANDOM."""

    RANDOM = "random"  # a random Shikimori/TMDB frame, as before the panel existed
    AUTHOR = "author"  # only the author's list; none left means none sold
    BOTH = "both"  # the author's list first, then random frames


class CurrencyReason(enum.StrEnum):
    """Why a `currency_transfers` row moved currency — see
    services/economy/. A `StrEnum` stored in a plain `String` column
    (same reasoning as `Provider` above), not a native `sa.Enum`: later
    economy phases add members, and a MariaDB ENUM would need an
    ALTER-widening migration every time."""

    GRANT = "grant"
    WRONG_GUESS = "wrong_guess"
    FIRST_GUESS = "first_guess"
    WIN = "win"
    SETTER = "setter"
    PROMPT_TURN = "prompt_turn"
    CLUE_PURCHASE = "clue_purchase"
    REFUND = "refund"
    BOUNTY = "bounty"
    BOUNTY_WIN = "bounty_win"
    TIP = "tip"
    SHARPEN = "sharpen"
    CASHBACK = "cashback"
    COMPENSATION = "compensation"
    ACHIEVEMENT = "achievement"
    SEASON_WIN = "season_win"
    SEASON_HOST = "season_host"
    SEASON_FIRST_GUESS = "season_first_guess"
    TIER_REWARD = "pass_reward"
    PREMIUM = "premium_pass"
    SEASON_CONVERSION = "season_conversion"
    # 🪙 spent in the Season Shop
    SEASON_SHOP = "season_shop"
    # 🪙 paid for completing a season objective
    SEASON_OBJECTIVE = "season_objective"
    # 🪙 paid for an omikuji slip
    SEASON_OMIKUJI = "season_omikuji"
    # 🪙 spent casting a hex; paid to the caster when a kappa hex triggers; lost to a kitsune
    HEX_CAST = "hex_cast"
    HEX_KAPPA = "hex_kappa"
    HEX_KITSUNE = "hex_kitsune"
    # 🪙 paid to a contributor when the group goal is reached
    SEASON_GOAL = "season_goal"


class Currency(enum.StrEnum):
    """Which currency a ledger row moves (seasons spec §2): the base currency
    (shown as 💠 to players; cached in Player.currency) or a season's coins
    (🪙; cached per season in season_balances). Plain string."""

    BASE = "base"
    SEASON = "season"


class CurrencyParty(enum.StrEnum):
    """One side of a currency transfer (services/economy/wallet.py). Stored as a
    plain string, like CurrencyReason."""

    HOUSE = "house"  # the game itself: currency is created (rewards) or spent (purchases)
    PLAYER = "player"  # a player's balance — the matching *_player_id is set
    POT = "pot"  # a game's bounty escrow (phase 3) — game_id is set


class ClueKind(enum.StrEnum):
    """Stored as a plain string, like CurrencyReason."""

    FIRST_LETTER = "first_letter"
    LAST_LETTER = "last_letter"
    TITLE_SHAPE = "title_shape"
    SCREENSHOT = "screenshot"
    TILE = "tile"


class EventType(enum.StrEnum):
    """What an `event_log` row records (services/events.py). Stored as a plain
    string, like CurrencyReason: the set grows with every consumer."""

    GAME_ACTIVATED = "game_activated"
    GUESS = "guess"
    GAME_WON = "game_won"
    GAME_UNSOLVED = "game_unsolved"
    STAGE_ADVANCED = "stage_advanced"
    VOTE_COUNTED = "vote_counted"
    OVERTHROWN = "overthrown"
    CURRENCY_MOVED = "currency_moved"
    CLUE_PURCHASED = "clue_purchased"
    CLUE_REFUNDED = "clue_refunded"
    CLUE_SHARED = "clue_shared"
    BOUNTY_SETTLED = "bounty_settled"
    # Seasons (Phase 5): emitted by season services so achievements can react.
    ITEM_GAINED = "item_gained"  # a season cosmetic was granted (any source)
    TRACK_COMPLETED = "pass_completed"  # a pass track finished; data["first"] for the free one
    SEASON_ENDED = "season_ended"  # one per ranked player when a season finalizes
    # Halloween season mechanics
    HEX_CAST = "hex_cast"
    HEX_BLOCKED = "hex_blocked"
    HEX_TRIGGERED = "hex_triggered"
    OMIKUJI_DRAWN = "omikuji_drawn"
    OFUDA_USED = "ofuda_used"
    GOAL_REACHED = "group_goal_reached"


class WinMethod(enum.StrEnum):
    """How a game was won, recorded on its `game_won` event."""

    GUESS = "guess"
    CORRECT = "correct"
    VOTE = "vote"
    SETWINNER = "setwinner"


class Rarity(enum.StrEnum):
    """An achievement tier's rarity band — sets its 💠 reward and points
    (services/achievements/rewards.py). Stored as a plain string."""

    BRONZE = "bronze"
    SILVER = "silver"
    GOLD = "gold"
    PLATINUM = "platinum"


class OutboxKind(enum.StrEnum):
    """What an announcement_outbox row will post. Stored as a plain string."""

    UNLOCK = "unlock"
    PERIOD_SUMMARY = "period_summary"
    SEASON_TEASER = "season_teaser"
    SEASON_START = "season_start"
    SEASON_END = "season_end"
    SEASON_TIER = "season_tier"
    SEASON_COMPLETE = "season_pass_done"
    # a player's DM for a completed season objective
    SEASON_OBJECTIVE = "season_objective"
    # Halloween: the group goal and the event day
    GOAL_MILESTONE = "goal_milestone"
    GOAL_REACHED = "goal_reached"
    EVENT_DAY_START = "event_day_start"
    EVENT_DAY_END = "event_day_end"
    # Halloween: a Kappa/Kitsune going off, or an omikuji self-prank
    HEX_NOTICE = "hex_notice"
    # Halloween: the boss game announcement, a Bone Collector find
    GOAL_BOSS = "goal_boss"
    BONE_FOUND = "bone_found"


class PeriodType(enum.StrEnum):
    """A champion period (services/achievements/periods.py). Plain string."""

    WEEK = "week"
    MONTH = "month"
    YEAR = "year"


class XpSource(enum.StrEnum):
    """Why a season_xp row was written (seasons spec §2). Plain string."""

    WIN = "win"
    HOST = "host"
    FIRST_GUESS = "first_guess"
    # a completed season objective (services/seasons/objectives.py)
    OBJECTIVE = "objective"


class SeasonStatus(enum.StrEnum):
    """A season_schedule row's lifecycle (seasons spec §1). Stored as a
    plain string. `closing`: past end_at, waiting for its tagged games."""

    SCHEDULED = "scheduled"
    ACTIVE = "active"
    CLOSING = "closing"
    ENDED = "ended"
    CANCELLED = "cancelled"


class HexOutcome(enum.StrEnum):
    """How a `hexes` row ended (Halloween season mechanics). Stored as a plain string."""

    BLOCKED = "blocked"
    TRIGGERED = "triggered"
    EXPIRED = "expired"
    CANCELLED = "cancelled"


class CosmeticSlot(enum.StrEnum):
    """Where an equipped season cosmetic shows (seasons spec §3). Stored as a plain string."""

    WIN_EFFECT = "win_effect"
    HOST_TRANSITION = "host_transition"
    HOST_FRAME = "host_frame"
    NAME_FLAIR = "name_flair"
    GUESS_REACTION = "guess_reaction"
    PROFILE_BANNER = "profile_banner"
    TITLE = "title"
    MASCOT_COSTUME = "mascot_costume"  # 🎭 Halloween: how Nani Pix looks on game pictures


class ItemSource(enum.StrEnum):
    """How an owned season cosmetic was obtained (seasons spec §3). Plain string."""

    ADMIN = "admin"
    TIER = "pass"
    SHOP = "shop"
    GOAL = "goal"  # the 100 Candles group goal: every contributor gets it
    BONES = "bones"  # a completed bone set


class PreviewKind(enum.StrEnum):
    """What an item's preview is (seasons spec §3): a line of text, an image or a clip.
    Plain string."""

    TEXT = "text"
    PHOTO = "photo"
    VIDEO = "video"


class ShowcaseKind(enum.StrEnum):
    """What a player pinned to their /profile card (#413): a season stamp (ref =
    run id) or an achievement (ref = achievement grant id). Plain string."""

    STAMP = "stamp"
    ACHIEVEMENT = "achievement"


class ConsumableKind(enum.StrEnum):
    """A consumable a player holds a count of (seasons spec §3): the clue token,
    plus the Halloween season's ofuda and omamori — no free sharpens."""

    # 🎟 the clue token; named so a security linter doesn't read "token" as a secret
    CLUE_TICKET = "clue_token"
    OFUDA = "ofuda"  # 📜 Halloween season consumable
    OMAMORI = "omamori"  # 🧿 Halloween season consumable
