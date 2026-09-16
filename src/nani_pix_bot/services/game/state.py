"""The core Game-row state machine — the only module that mutates a
Game row. See MECHANICS.md for the rules this implements. TurnState
bookkeeping (a related but distinct concern) lives in turns.py."""

import enum
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import NamedTuple

from loguru import logger
from sqlalchemy import select
from sqlalchemy.orm import Session

from nani_pix_bot.models.enums import GameStatus, PixelStage, Provider
from nani_pix_bot.models.game import Game
from nani_pix_bot.services import matching, players
from nani_pix_bot.services.game import turns
from nani_pix_bot.services.search.anilist import AniListResult
from nani_pix_bot.services.search.jikan import JikanResult
from nani_pix_bot.services.search.shikimori import ShikimoriResult
from nani_pix_bot.services.search.tmdb import TMDBResult
from nani_pix_bot.services.settings import stage_config

# Blockiest to clearest — see MECHANICS.md's "Pixelation stages" table.
# Fixed: the 5 PixelStage members and their order never change, only
# each stage's target width and wrong-guess limit (see
# services/settings/stage_config.py) are admin-configurable.
STAGE_ORDER = [
    PixelStage.STAGE_1,
    PixelStage.STAGE_2,
    PixelStage.STAGE_3,
    PixelStage.STAGE_4,
    PixelStage.STAGE_5,
]

# Absolute from game start, not reset by activity — see MECHANICS.md's
# "Timeout" section.
TIMEOUT_DURATION = timedelta(days=2)

# Absolute from SETUP creation, not extended by activity within setup —
# see MECHANICS.md's "Starting a game" section.
SETUP_ABANDON_DELAY = timedelta(hours=1)

# Reset on every /guess (right or wrong) — see MECHANICS.md's
# "Inactivity" section. Orthogonal to TIMEOUT_DURATION above: that one
# never resets, this pair does, on every guess. Worst case (a game that
# never gets a single guess) resolves to UNSOLVED via 5 * 6h = 30h,
# comfortably inside the 2-day absolute backstop.
INACTIVITY_NUDGE_DELAY = timedelta(hours=3)
INACTIVITY_ADVANCE_DELAY = timedelta(hours=6)


class GuessOutcome(enum.Enum):
    """What a /guess attempt did to the game — tells the command layer
    which reply/image to send. Not persisted."""

    WON = "won"
    WRONG = "wrong"
    STAGE_ADVANCED = "stage_advanced"
    UNSOLVED = "unsolved"


@dataclass(frozen=True)
class TitleVariants:
    """The title fields a search result or Game might have — not every
    source has all four (AniList never has `russian`, Shikimori never
    has `native`), so every field defaults to unset."""

    english: str | None = None
    romaji: str | None = None
    native: str | None = None
    russian: str | None = None


def prioritized_title(variants: TitleVariants, *, lang: str) -> str:
    """The shared priority-order rule behind both display_title() below
    and the DM setup's AniList/Shikimori result-picker button labels
    (commands/dm_start/keyboards.py) — the exact same rule has to pick
    both, or the button a starter taps can show a different title than
    what the confirmation preview then calls that same pick. RU-language
    bots prefer the Russian title first; otherwise English leads. Falls
    back to "?" if every field is unset."""
    candidates = (
        (variants.russian, variants.english, variants.romaji, variants.native)
        if lang.upper() == "RU"
        else (variants.english, variants.romaji, variants.native, variants.russian)
    )
    return next((title for title in candidates if title), "?")


def display_title(game: Game, lang: str) -> str:
    """Best available display title for a Game, for captions and the DM
    setup preview — see prioritized_title() for the fallback order.
    Three of the four call sites that used to keep their own copy of
    this fallback chain (commands/game_flow/guess.py, correct.py,
    stop.py) were missing the Russian fallback entirely (a
    Shikimori-only result would show "?" instead of its actual
    title)."""
    variants = TitleVariants(
        english=game.title_english,
        romaji=game.title_romaji,
        native=game.title_native,
        russian=game.title_russian,
    )
    return prioritized_title(variants, lang=lang)


def match_candidates(game: Game) -> list[str]:
    """Every string a /guess is matched against for this game — the
    single source of truth for both record_guess() and the DM setup
    preview (commands/dm_start/preview.py), so what's shown to the
    starter as an accepted answer can never drift from what actually
    is one. Filters out unset fields (e.g. title_native is never set
    for a Shikimori-sourced game) and an empty/missing synonyms list."""
    return [
        candidate
        for candidate in (
            game.title_romaji,
            game.title_english,
            game.title_native,
            game.title_russian,
            *(game.synonyms or []),
        )
        if candidate
    ]


# The three providers that can supply screenshots — AniList identifies
# an anime but has no screenshot endpoint, so it is the one Provider a
# screenshot-shaped payload must be rejected for. Moved here from
# commands/dm_start/keyboards.py (issue #159) so services/game/autostart.py
# can reuse the same ordering rule without services/ importing commands/.
SCREENSHOT_CAPABLE_PROVIDERS: tuple[Provider, ...] = (
    Provider.SHIKIMORI,
    Provider.JIKAN,
    Provider.TMDB,
)


def screenshot_capable_providers(game: Game) -> list[Provider]:
    """All 3 screenshot-capable providers, same-provider-as-identification
    first when it's one of them (so the common case — screenshot source
    matches identification source — needs no cross-provider search at
    all). Every provider is offered regardless of whether the game
    already has an id for it — a caller not finding one triggers
    cross-provider resolution (see commands/dm_start/screenshots.py's
    _resolve_screenshot_source, and services/game/autostart.py's
    _pick_screenshot for the bot-initiated equivalent).

    `game.source` arrives as a bare str (the Provider columns are
    String-backed, not a native enum — see models/game.py), and can
    legitimately be "manual", which is in neither list, so the
    membership test settles both questions at once."""
    candidates = list(SCREENSHOT_CAPABLE_PROVIDERS)
    if game.source in candidates:
        identified_by = Provider(game.source)
        candidates.remove(identified_by)
        candidates.insert(0, identified_by)
    return candidates


def active_or_setup_game(session: Session) -> Game | None:
    """The one game currently SETUP or ACTIVE, if any — there's never more
    than one (enforced here, not by a DB constraint; see ARCHITECTURE.md)."""
    stmt = select(Game).where(Game.status.in_([GameStatus.SETUP, GameStatus.ACTIVE]))
    return session.scalars(stmt).first()


def active_games(session: Session) -> list[Game]:
    """Every ACTIVE game — normally at most one (see active_or_setup_game),
    but this scans without that assumption for startup timeout re-arming."""
    stmt = select(Game).where(Game.status == GameStatus.ACTIVE)
    return list(session.scalars(stmt))


def setup_games(session: Session) -> list[Game]:
    """Every SETUP game — normally at most one (see active_or_setup_game),
    but this scans without that assumption for startup setup-abandon
    timer re-arming."""
    stmt = select(Game).where(Game.status == GameStatus.SETUP)
    return list(session.scalars(stmt))


def get_setup_game_for_starter(session: Session, starter_id: int) -> Game | None:
    """The SETUP game `starter_id` is currently picking an anime for, if
    any. Deriving this from the DB (rather than caching it in PTB's
    in-memory user_data) means the DM setup flow survives a bot restart —
    see the incident that prompted this in issue #11."""
    stmt = select(Game).where(Game.status == GameStatus.SETUP, Game.starter_id == starter_id)
    return session.scalars(stmt).first()


def can_start(session: Session, user_id: int) -> bool:
    """Whether `user_id` may DM the bot a screenshot to start a new game
    right now — see MECHANICS.md's "Starting a game"."""
    if active_or_setup_game(session) is not None:
        return False
    turn_state = turns.get_turn_state(session)
    return turn_state is None or turn_state.next_starter_id in (None, user_id)


def create_setup_game(
    session: Session, *, starter_id: int, original_image: bytes | None = None
) -> Game:
    """`original_image` is optional — the traditional photo-first entry
    point always has bytes in hand immediately; the screenshot-less
    /newgame entry point creates the row before any image exists yet
    (it's filled in once a screenshot is picked, later in the flow)."""
    game = Game(
        starter_id=starter_id,
        original_image=original_image,
        status=GameStatus.SETUP,
        setup_deadline=datetime.now(UTC) + SETUP_ABANDON_DELAY,
    )
    session.add(game)
    session.flush()  # populate game.id for the caller without a full commit
    logger.info("Game {} created (SETUP) by starter {}", game.id, starter_id)
    return game


def stage_result(
    game: Game,
    result: AniListResult | ShikimoriResult | JikanResult | TMDBResult,
    *,
    source: Provider,
) -> None:
    """Assign a picked search result's title/synonyms onto a still-SETUP
    game — doesn't post anything or change status. This is the shared
    landing spot for every identification method (AniList, Shikimori,
    Jikan, TMDB, and eventually manual entry); the confirmation-screen
    ticket (#19) is what will show a preview between this and
    activate_game().

    `source` is a `Provider`, never `"manual"`: every caller arrives
    holding a real result from one of the four services. Manual entry has
    no result to stage and goes through `stage_manual_entry` below, which
    writes the bare `"manual"` string itself."""
    game.title_romaji = result.title_romaji
    game.title_english = result.title_english
    game.synonyms = result.synonyms
    game.source = source
    if isinstance(result, AniListResult):
        game.anilist_id = result.anilist_id
        game.title_native = result.title_native
    elif isinstance(result, ShikimoriResult):
        game.shikimori_id = result.shikimori_id
        game.title_russian = result.title_russian
    elif isinstance(result, JikanResult):
        game.jikan_id = result.jikan_id
        game.title_native = result.title_native
    elif isinstance(result, TMDBResult):
        game.tmdb_id = result.tmdb_id
        game.title_native = result.title_native


def set_screenshot_provider_id(
    game: Game, result: ShikimoriResult | JikanResult | TMDBResult
) -> None:
    """Cross-provider screenshot resolution's equivalent of stage_result()
    (see commands/dm_start/screenshots.py's and screenshot_gallery.py's
    ticket 8): records a screenshot provider's id on the game without
    touching the identification fields (title/synonyms/source)
    stage_result() sets — resolving a screenshot from a different
    provider than the one that identified this anime shouldn't
    overwrite that identification."""
    if isinstance(result, ShikimoriResult):
        game.shikimori_id = result.shikimori_id
    elif isinstance(result, JikanResult):
        game.jikan_id = result.jikan_id
    elif isinstance(result, TMDBResult):
        game.tmdb_id = result.tmdb_id


def stage_manual_entry(game: Game, *, title: str, synonyms: list[str]) -> None:
    """Manual entry's equivalent of stage_result() — there's no external
    search result to draw from, just what the starter typed."""
    game.title_english = title
    game.synonyms = synonyms
    game.source = "manual"


def activate_game(session: Session, game: Game) -> None:
    """Finalize game setup once a result has been staged (see
    stage_result): move to the first stage and open the turn (the
    designated starter's turn is now consumed)."""
    game.status = GameStatus.ACTIVE
    game.current_stage = STAGE_ORDER[0]
    game.wrong_guess_count = 0
    game.scheduled_end_at = datetime.now(UTC) + TIMEOUT_DURATION
    reset_inactivity_clock(game)

    turn_state = turns.get_or_create_turn_state(session)
    turn_state.next_starter_id = None
    logger.info("Game {} activated (source={})", game.id, game.source)


def record_guess(session: Session, game: Game, *, guesser_id: int, guess_text: str) -> GuessOutcome:
    """Apply one /guess attempt to an ACTIVE game — see MECHANICS.md's
    "Guess matching" and "Pixelation stages" sections."""
    if game.current_stage is None:
        msg = f"record_guess called on game {game.id} with no current_stage (not ACTIVE?)"
        logger.warning(msg)
        raise ValueError(msg)

    game.total_guess_count += 1

    candidates = match_candidates(game)
    matched = matching.is_match(guess_text, candidates)
    logger.debug(
        "Game {}: guesser {} guessed {!r} against {} candidates -> {}",
        game.id,
        guesser_id,
        guess_text,
        len(candidates),
        "match" if matched else "no match",
    )
    if matched:
        _win(session, game, winner_id=guesser_id)
        return GuessOutcome.WON

    game.wrong_guess_count += 1
    limit = stage_config.get_stage_config(session, game.current_stage).wrong_guess_limit
    if game.wrong_guess_count < limit:
        logger.debug(
            "Game {}: wrong guess {}/{} at stage {}",
            game.id,
            game.wrong_guess_count,
            limit,
            game.current_stage,
        )
        return GuessOutcome.WRONG

    return advance_stage(game)


def reset_inactivity_clock(game: Game) -> None:
    """(Re)starts the inactivity nudge/auto-advance clock from now — see
    INACTIVITY_NUDGE_DELAY/INACTIVITY_ADVANCE_DELAY. Called on
    activation, after every guess (guess.py), and after every
    inactivity-driven auto-advance (jobs/timers.py) — so the clock
    always measures time since the most recent guess or auto-advance,
    whichever happened last."""
    now = datetime.now(UTC)
    game.inactivity_nudge_at = now + INACTIVITY_NUDGE_DELAY
    game.inactivity_advance_at = now + INACTIVITY_ADVANCE_DELAY


def clear_inactivity_nudge(game: Game) -> None:
    """Clears `inactivity_nudge_at` after the nudge has actually been sent
    (jobs/timers.py's inactivity_nudge_job_callback) — the nudge is a
    one-shot reminder within a silence window, not a repeating one, so
    unlike reset_inactivity_clock() this leaves inactivity_advance_at
    alone; it keeps counting toward its own separate 6h deadline.

    Without this, inactivity_nudge_at stays stuck in the past once it's
    due, and since JobQueue jobs never survive a process restart,
    rearm_pending_timeouts re-derives an already-overdue nudge job from
    that stale timestamp on every subsequent redeploy — reposting the
    same nudge on every restart until the next real /guess or
    auto-advance resets the clock."""
    game.inactivity_nudge_at = None


def advance_stage(game: Game) -> GuessOutcome:
    """Move `game` to the next PixelStage, or end it UNSOLVED if it was
    already on the last one — the shared landing spot for both a
    guess-driven stage exhaustion (record_guess, above) and the
    inactivity-driven auto-advance (jobs/timers.py's
    inactivity_advance_job_callback), so the two paths can never drift
    apart. Resets wrong_guess_count same as the guess-driven path did
    inline before this was extracted."""
    # Every caller only invokes this on an ACTIVE game with a stage
    # already set (record_guess checks this itself; the inactivity job
    # callback re-checks status == ACTIVE before calling in). This used
    # to be a bare `assert` on the reasoning that it's an internal
    # invariant, not user input to validate — but S101 (issue #117)
    # reverses that: a bare assert silently vanishes under `python -O`.
    # Still an internal invariant rather than user input (hence
    # RuntimeError, not record_guess's ValueError above), just enforced
    # with a real exception now instead of one that could disappear.
    if game.current_stage is None:
        msg = f"advance_stage called on game {game.id} with no current_stage"
        logger.error(msg)
        raise RuntimeError(msg)
    game.wrong_guess_count = 0
    next_index = STAGE_ORDER.index(game.current_stage) + 1
    if next_index >= len(STAGE_ORDER):
        force_unsolved(game)
        return GuessOutcome.UNSOLVED

    game.current_stage = STAGE_ORDER[next_index]
    logger.info("Game {} advanced to stage {}", game.id, game.current_stage)
    return GuessOutcome.STAGE_ADVANCED


class StageProgress(NamedTuple):
    """Where an ACTIVE game stands within its pixelation stages. Both
    `remaining` and `limit` are reported because every player-facing
    guess counter shows them as a pair ("2/3") — the bare remaining count
    on its own says nothing about how generous the stage was to start
    with. See MECHANICS.md's "Pixelation stages"."""

    number: int
    total: int
    remaining: int
    limit: int


def stage_progress(session: Session, game: Game) -> StageProgress:
    """Current stage number/total and the wrong-guess budget left at it,
    for a still-ACTIVE game — feeds the /guess wrong-feedback message and
    the stage-advance caption."""
    if game.current_stage is None:
        msg = f"stage_progress called on game {game.id} with no current_stage (not ACTIVE?)"
        raise ValueError(msg)

    stage_number = STAGE_ORDER.index(game.current_stage) + 1
    limit = stage_config.get_stage_config(session, game.current_stage).wrong_guess_limit
    remaining = limit - game.wrong_guess_count
    return StageProgress(stage_number, len(STAGE_ORDER), remaining, limit)


def force_win(session: Session, game: Game, *, winner_id: int) -> None:
    """The author-override path (/correct) — identical end state to an
    automatic match in record_guess, just triggered without one."""
    _win(session, game, winner_id=winner_id)


def _win(session: Session, game: Game, *, winner_id: int) -> None:
    game.status = GameStatus.WON
    game.winner_id = winner_id

    winner = players.get_or_create_player(session, winner_id)
    winner.wins += 1

    turns.set_next_starter(session, winner_id)
    logger.info("Game {} won by player {}", game.id, winner_id)


def force_unsolved(game: Game) -> None:
    """Ends a game unsolved — used both by record_guess's stage-exhaustion
    path and by the timeout job callback. See MECHANICS.md's "Ending
    unsolved" section."""
    game.status = GameStatus.UNSOLVED
    logger.info("Game {} ended unsolved", game.id)


def has_answer_to_reveal(game: Game) -> bool:
    """Whether `game` still has an answer the group is waiting on — an
    ACTIVE round with its original image intact. Drives `/stop`'s "stop
    and reveal" button (commands/game_flow/stop.py, and the same keyboard
    offered by commands/stageconfig.py when a config edit is blocked): a
    SETUP game has never posted anything to the topic, and may not even
    have a title or an image staged yet, so there's nothing to reveal.

    Status is the whole test, deliberately. An ACTIVE game always still
    has its bytes — clear_original_screenshot() only ever runs on a game
    that has just reached a terminal outcome — so `original_image is not
    None` added no information, and reading it here cost the entire blob:
    models/game.py defers that column precisely so a query that only
    wants to know *about* a game doesn't drag a multi-hundred-KB payload
    across with it. Same reasoning as commands/game_flow/guess.py's
    _validate_guess, which won't touch it either.

    What this drives is a button, not the reveal itself. The two places
    that actually post one — stop.py's _announce_stop and jobs/timers.py's
    timeout callback — re-check the bytes where they are about to be
    used, which is the one place the check is both free (they need them
    loaded anyway) and load-bearing; _announce_stop falls back to the
    plain notice and a WARNING if they are somehow gone."""
    return game.status == GameStatus.ACTIVE


def clear_original_screenshot(game: Game) -> None:
    """Drop the stored image bytes once a reveal message is confirmed
    sent — see MECHANICS.md's "Cleanup" note."""
    game.original_image = None
