"""The core Game-row state machine — the only module that mutates a
Game row. See MECHANICS.md for the rules this implements. TurnState
bookkeeping (a related but distinct concern) lives in turns.py."""

import enum
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any, NamedTuple

from loguru import logger
from sqlalchemy import select
from sqlalchemy.orm import Session, object_session

from nani_pix_bot import log_context
from nani_pix_bot.models.enums import EventType, GameStatus, PixelStage, Provider, WinMethod
from nani_pix_bot.models.game import Game
from nani_pix_bot.services import events, i18n, matching, players
from nani_pix_bot.services.clues import text as clue_text
from nani_pix_bot.services.game import guesses, turns
from nani_pix_bot.services.game.clock import deadline_after
from nani_pix_bot.services.game.win_facts import win_facts
from nani_pix_bot.services.search.anilist import AniListResult
from nani_pix_bot.services.search.shikimori import ShikimoriResult
from nani_pix_bot.services.search.tenrai import TenraiResult
from nani_pix_bot.services.search.tmdb import TMDBResult
from nani_pix_bot.services.seasons import schedule as season_schedule
from nani_pix_bot.services.settings import bot_settings, stage_config

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


def stage_label(stage: PixelStage) -> str:
    """`stage 2/5` — how a stage reads in log lines, rather than the
    `PixelStage.STAGE_2` enum repr."""
    return f"stage {STAGE_ORDER.index(stage) + 1}/{len(STAGE_ORDER)}"


@dataclass(frozen=True)
class WinTerms:
    """What a win is worth and how it came about: the wins-counter `award`
    (HARD MODE wins count more) and the `how` recorded on its `game_won`
    event. Bundled so _win/_record_win stay under qlty's parameter limit."""

    award: int = 1
    how: WinMethod = WinMethod.GUESS


class GuessOutcome(enum.Enum):
    """What a /guess attempt did to the game — tells the command layer
    which reply/image to send. Not persisted."""

    WON = "won"
    WRONG = "wrong"
    STAGE_ADVANCED = "stage_advanced"
    TURN_ADVANCED = "turn_advanced"
    UNSOLVED = "unsolved"
    VOTE_OPENED = "vote_opened"


@dataclass(frozen=True)
class TitleVariants:
    """The title fields a search result or Game might have — not every
    source has all four (AniList never has `russian`, Shikimori never
    has `native`), so every field defaults to unset."""

    english: str | None = None
    romaji: str | None = None
    native: str | None = None
    russian: str | None = None


class TitleField(enum.StrEnum):
    """Which of a game's title fields a value came from — clues name it
    to the player (services/clues/), so a fallback title is never passed
    off as the group-language one."""

    ENGLISH = "english"
    ROMAJI = "romaji"
    NATIVE = "native"
    RUSSIAN = "russian"


def prioritized_title_field(variants: TitleVariants, *, lang: str) -> tuple[TitleField, str] | None:
    """The priority-order rule behind prioritized_title(), plus which
    field won. RU-language bots prefer the Russian title first; otherwise
    English leads. None if every field is unset."""
    order = (
        (TitleField.RUSSIAN, TitleField.ENGLISH, TitleField.ROMAJI, TitleField.NATIVE)
        if lang.upper() == "RU"
        else (TitleField.ENGLISH, TitleField.ROMAJI, TitleField.NATIVE, TitleField.RUSSIAN)
    )
    for field in order:
        title = getattr(variants, field.value)
        if title:
            return field, title
    return None


def prioritized_title(variants: TitleVariants, *, lang: str) -> str:
    """The shared priority-order rule behind both display_title() below
    and the DM setup's AniList/Shikimori result-picker button labels
    (commands/dm_start/keyboards.py) — the exact same rule has to pick
    both, or the button a starter taps can show a different title than
    what the confirmation preview then calls that same pick. RU-language
    bots prefer the Russian title first; otherwise English leads. Falls
    back to "?" if every field is unset."""
    picked = prioritized_title_field(variants, lang=lang)
    return "?" if picked is None else picked[1]


def _variants(game: Game) -> TitleVariants:
    return TitleVariants(
        english=game.title_english,
        romaji=game.title_romaji,
        native=game.title_native,
        russian=game.title_russian,
    )


def display_title(game: Game, lang: str) -> str:
    """Best available display title for a Game, for captions and the DM
    setup preview — see prioritized_title() for the fallback order.
    Three of the four call sites that used to keep their own copy of
    this fallback chain (commands/game_flow/guess.py, correct.py,
    stop.py) were missing the Russian fallback entirely (a
    Shikimori-only result would show "?" instead of its actual
    title)."""
    return prioritized_title(_variants(game), lang=lang)


def display_title_field(game: Game, lang: str) -> tuple[TitleField, str] | None:
    """display_title() plus which field it came from; None if the game
    has no title at all."""
    return prioritized_title_field(_variants(game), lang=lang)


def game_id_line(game_id: int, lang: str) -> str:
    """The `🎲 Game #<id>` line every game post ends with (issue #248), so an
    admin can name the game in /setwinner or /refund. Leading newline: callers
    just append it."""
    return "\n" + i18n.t("game.id_line", lang, id=game_id)


def clue_titles(game: Game, lang: str) -> list[tuple[TitleField, str]]:
    """The titles a text clue covers, in order: the group-language one
    (display_title_field(), fallback chain included), then romaji, then
    English, each field once and each text once (case-insensitive). The
    native title only appears when it is that fallback default. Empty if
    the game has no title at all."""
    default = display_title_field(game, lang)
    if default is None:
        return []
    listed = [default]
    seen = {default[1].strip().casefold()}
    for field in (TitleField.ROMAJI, TitleField.ENGLISH):
        title = getattr(game, f"title_{field.value}")
        if not title or field is default[0] or title.strip().casefold() in seen:
            continue
        seen.add(title.strip().casefold())
        listed.append((field, title))
    return listed


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
    Provider.TENRAI,
    Provider.TMDB,
)


def screenshot_capable_providers(game: Game) -> list[Provider]:
    """All 3 screenshot-capable providers, same-provider-as-identification
    first when it's one of them (so the common case — screenshot source
    matches identification source — needs no cross-provider search at
    all). Every provider is offered regardless of whether the game
    already has an id for it — a caller not finding one triggers
    cross-provider resolution (see commands/dm_start/screenshots.py's
    _fetch_for_pick, and services/game/autostart.py's
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


def _bound(game: Game | None) -> Game | None:
    """Attach a looked-up game to the structured log context (issue #230):
    the lookups here are how nearly every handler finds its game, so
    binding at this one spot gives every later line of the update its
    `game_id` without each caller doing it."""
    log_context.bind_game(game)
    return game


def active_or_setup_game(session: Session) -> Game | None:
    """The one game currently SETUP, ACTIVE or VOTING (an open hard-mode vote
    still holds the round — issue #252), if any; there's never more than one
    (enforced here, not by a DB constraint; see ARCHITECTURE.md)."""
    stmt = select(Game).where(
        Game.status.in_([GameStatus.SETUP, GameStatus.ACTIVE, GameStatus.VOTING])
    )
    return _bound(session.scalars(stmt).first())


def active_games(session: Session) -> list[Game]:
    """Every ACTIVE game — normally at most one (see active_or_setup_game),
    but this scans without that assumption for startup timeout re-arming."""
    stmt = select(Game).where(Game.status == GameStatus.ACTIVE)
    return list(session.scalars(stmt))


def voting_games(session: Session) -> list[Game]:
    """Every VOTING game - normally at most one, scanned without that
    assumption for startup vote-close timer re-arming."""
    stmt = select(Game).where(Game.status == GameStatus.VOTING)
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
    return _bound(session.scalars(stmt).first())


def can_start(session: Session, user_id: int) -> bool:
    """Whether `user_id` may DM the bot a screenshot to start a new game
    right now — see MECHANICS.md's "Starting a game"."""
    if active_or_setup_game(session) is not None:
        return False
    turn_state = turns.get_turn_state(session)
    return turn_state is None or turn_state.next_starter_id in (None, user_id)


def create_setup_game(
    session: Session,
    *,
    starter_id: int,
    original_image: bytes | None = None,
    entry: str | None = None,
) -> Game:
    """`original_image` is optional — the traditional photo-first entry
    point always has bytes in hand immediately; the screenshot-less
    /newgame entry point creates the row before any image exists yet
    (it's filled in once a screenshot is picked, later in the flow).
    `entry` names how the game was started, for the log; it defaults to
    whichever of those two the image implies."""
    turn_state = turns.get_turn_state(session)
    game = Game(
        starter_id=starter_id,
        original_image=original_image,
        status=GameStatus.SETUP,
        turn_received_at=turn_state.turn_received_at if turn_state is not None else None,
        setup_deadline=deadline_after(session, SETUP_ABANDON_DELAY),
    )
    session.add(game)
    session.flush()  # populate game.id for the caller without a full commit
    log_context.bind_game(game)
    if entry is None:
        entry = "DM photo" if original_image is not None else "/newgame"
    logger.info(
        "created (SETUP) by {starter} via {entry}",
        starter=players.describe_player_id(session, starter_id),
        starter_id=starter_id,
        entry=entry,
        game_id=game.id,
    )
    return game


def stage_result(
    game: Game,
    result: AniListResult | ShikimoriResult | TenraiResult | TMDBResult,
    *,
    source: Provider,
) -> None:
    """Assign a picked search result's title/synonyms onto a still-SETUP
    game — doesn't post anything or change status. This is the shared
    landing spot for every identification method (AniList, Shikimori,
    Tenrai, TMDB, and eventually manual entry); the confirmation-screen
    ticket (#19) is what will show a preview between this and
    activate_game().

    `source` is a `Provider`, never `"manual"`: every caller arrives
    holding a real result from one of the four services. Manual entry has
    no result to stage and goes through `stage_manual_entry` below, which
    writes the bare `"manual"` string itself.

    Starts from `clear_identification` because each provider fills in
    only its own subset of fields — see that function's docstring."""
    clear_identification(game)
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
    elif isinstance(result, TenraiResult):
        game.tenrai_id = result.tenrai_id
        game.title_native = result.title_native
    elif isinstance(result, TMDBResult):
        game.tmdb_id = result.tmdb_id
        game.title_native = result.title_native


def set_screenshot_provider_id(
    game: Game, result: ShikimoriResult | TenraiResult | TMDBResult
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
    elif isinstance(result, TenraiResult):
        game.tenrai_id = result.tenrai_id
    elif isinstance(result, TMDBResult):
        game.tmdb_id = result.tmdb_id


def clear_identification(game: Game) -> None:
    """Forget which anime a still-SETUP game was identified as: every
    title variant, the synonyms, every provider id, the creator's
    "numbers matter" answer and any alias suggestions (both were about
    the old titles).

    Each provider's result fills in only its own subset of these (only
    Shikimori has `title_russian`, only AniList/Tenrai/TMDB have
    `title_native`, each sets just its own id), so overwriting field by
    field left an earlier pick's leftovers behind whenever the starter
    re-identified with a different provider — issue #198, where the old
    anime's Russian title became both the RU preview's Title and an
    accepted /guess. Called before every new identification is staged,
    and when a new identification method is picked.

    The screenshot columns (`screenshot_source`, `original_image`) are
    not touched — that's `commands/dm_start/screenshots.py`'s
    `clear_screenshot_selection`, which the preview's "Re-search" runs
    before any of this can happen."""
    logger.debug("clearing previous identification (source={source})", source=game.source)
    game.title_romaji = None
    game.title_english = None
    game.title_native = None
    game.title_russian = None
    game.synonyms = None
    game.anilist_id = None
    game.shikimori_id = None
    game.tenrai_id = None
    game.tmdb_id = None
    game.numbers_matter = None
    game.alias_suggestions = None


def stage_manual_entry(game: Game, *, title: str, synonyms: list[str]) -> None:
    """Manual entry's equivalent of stage_result() — there's no external
    search result to draw from, just what the starter typed. Clears any
    earlier identification first, same as stage_result()."""
    clear_identification(game)
    game.title_english = title
    game.synonyms = synonyms
    game.source = "manual"


def activate_game(session: Session, game: Game) -> None:
    """Finalize game setup once a result has been staged (see
    stage_result): move to the first stage and open the turn (the
    designated starter's turn is now consumed)."""
    game.status = GameStatus.ACTIVE
    game.activated_at = datetime.now(UTC)
    game.season_id = season_schedule.active_id(session)
    if game.hard_mode:
        game.hard_mode_turn = 1
    else:
        game.current_stage = STAGE_ORDER[0]
    game.wrong_guess_count = 0
    game.scheduled_end_at = deadline_after(session, TIMEOUT_DURATION)
    reset_inactivity_clock(session, game)

    turn_state = turns.get_or_create_turn_state(session)
    turn_state.next_starter_id = None
    logger.info(
        "ACTIVE — started by {starter}, source={source}, answer {answer!r}, hard_mode={hard_mode}",
        starter=players.describe_player_id(session, game.starter_id),
        starter_id=game.starter_id,
        source=game.source,
        answer=display_title(game, "EN"),
        hard_mode=game.hard_mode,
        season_id=game.season_id,
        game_id=game.id,
    )
    events.emit(
        session,
        EventType.GAME_ACTIVATED,
        events.Involved(actor_id=game.starter_id, game_id=game.id),
        source=str(game.source),
        hard_mode=game.hard_mode,
        own_screenshot=game.screenshot_source is None,
    )


def is_correct_guess(game: Game, guess_text: str) -> bool:
    """Whether a guess names this game's anime — see MECHANICS.md's
    "Guess matching"."""
    return matching.is_match(
        guess_text, match_candidates(game), numbers_matter=bool(game.numbers_matter)
    )


def guess_score(game: Game, guess_text: str) -> float:
    """How close a wrong guess came (matching.best_score)."""
    return matching.best_score(
        guess_text, match_candidates(game), numbers_matter=bool(game.numbers_matter)
    )


def partial_reveal_text(session: Session, game: Game, guess_text: str) -> str | None:
    """The masked title a wrong guess partly matched (issue #250), or None."""
    match = matching.partial_match(
        guess_text,
        match_candidates(game),
        min_letters=bot_settings.get_partial_match_min_letters(session),
        numbers_matter=bool(game.numbers_matter),
    )
    if match is None:
        return None
    return clue_text.words_shape(
        match.candidate, match.word_indices, numbers_matter=bool(game.numbers_matter)
    )


def record_guess(session: Session, game: Game, *, guesser_id: int, guess_text: str) -> GuessOutcome:
    """Apply one /guess attempt to an ACTIVE game — see MECHANICS.md's
    "Guess matching" and "Pixelation stages" sections."""
    if game.hard_mode:
        # Local import, not module-level: hard_mode.py imports this
        # module at module level (it's the primary direction of the
        # dependency — see its own docstring), so a module-level import
        # back here would be a genuine circular import. Same idiom, same
        # reasoning, as models/enums.py's Provider.screenshot_module/
        # search_module properties.
        from nani_pix_bot.services.game import hard_mode

        outcome = hard_mode.record_hard_mode_guess(
            session, game, guesser_id=guesser_id, guess_text=guess_text
        )
        if outcome is GuessOutcome.UNSOLVED:
            turns.mark_turn_open_if_unassigned(session)
        return outcome

    if game.current_stage is None:
        msg = f"record_guess called on game {game.id} with no current_stage (not ACTIVE?)"
        logger.warning(msg)
        raise ValueError(msg)

    stage = stage_label(game.current_stage)
    game.total_guess_count += 1

    # The guesser is the update's own user (the /guess handler is the only
    # caller), so the log context already names them; see log_context.py.
    correct = is_correct_guess(game, guess_text)
    reveal = None if correct else partial_reveal_text(session, game, guess_text)
    if reveal is not None:
        logger.info("partial match revealed {reveal!r}", reveal=reveal, game_id=game.id)
    guesses.log_guess(
        session,
        game,
        guesses.GuessRecord(
            player_id=guesser_id,
            text=guess_text,
            stage=STAGE_ORDER.index(game.current_stage) + 1,
            correct=correct,
            partial_reveal=reveal,
            score=None if correct else guess_score(game, guess_text),
        ),
    )
    if correct:
        logger.info(
            "guessed {guess!r} — CORRECT at {stage}", guess=guess_text, stage=stage, game_id=game.id
        )
        _win(session, game, winner_id=guesser_id)
        return GuessOutcome.WON

    game.wrong_guess_count += 1
    limit = stage_config.get_stage_config(session, game.current_stage).wrong_guess_limit
    logger.info(
        "guessed {guess!r} — wrong at {stage} ({wrong}/{limit})",
        guess=guess_text,
        stage=stage,
        wrong=game.wrong_guess_count,
        limit=limit,
        game_id=game.id,
    )
    if game.wrong_guess_count < limit:
        return GuessOutcome.WRONG

    outcome = advance_stage(game, reason="wrong-guess limit reached")
    if outcome is GuessOutcome.UNSOLVED:
        turns.mark_turn_open_if_unassigned(session)
    return outcome


def reset_inactivity_clock(session: Session, game: Game) -> None:
    """(Re)starts the inactivity nudge/auto-advance clock from now — see
    INACTIVITY_NUDGE_DELAY/INACTIVITY_ADVANCE_DELAY. Quiet time doesn't
    count toward either (see clock.deadline_after). Called on
    activation, after every guess (guess.py), and after every
    inactivity-driven auto-advance (jobs/timers.py) — so the clock
    always measures time since the most recent guess or auto-advance,
    whichever happened last."""
    game.inactivity_nudge_at = deadline_after(session, INACTIVITY_NUDGE_DELAY)
    game.inactivity_advance_at = deadline_after(session, INACTIVITY_ADVANCE_DELAY)


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


def advance_stage(game: Game, *, reason: str) -> GuessOutcome:
    """Move `game` to the next PixelStage, or end it UNSOLVED if it was
    already on the last one — the shared landing spot for both a
    guess-driven stage exhaustion (record_guess, above) and the
    inactivity-driven auto-advance (jobs/timers.py's
    inactivity_advance_job_callback), so the two paths can never drift
    apart. Resets wrong_guess_count same as the guess-driven path did
    inline before this was extracted. `reason` says what triggered the
    advance, for the log."""
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
        force_unsolved(game, cause=f"final stage exhausted — {reason}")
        return GuessOutcome.UNSOLVED

    next_stage = STAGE_ORDER[next_index]
    game.current_stage = next_stage
    logger.info(
        "advanced to {stage} ({reason})",
        stage=stage_label(next_stage),
        reason=reason,
        game_id=game.id,
    )
    _emit_detached(
        game,
        EventType.STAGE_ADVANCED,
        events.Involved(game_id=game.id),
        from_stage=next_index,
        to_stage=next_index + 1,
        reason=reason,
    )
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
    if game.hard_mode:
        # Local import — see record_guess's identical guard above for why.
        from nani_pix_bot.services.game import hard_mode

        terms = WinTerms(award=hard_mode.HARD_MODE_WIN_AWARD, how=WinMethod.CORRECT)
    else:
        terms = WinTerms(how=WinMethod.CORRECT)
    _win(session, game, winner_id=winner_id, terms=terms)


def _win(session: Session, game: Game, *, winner_id: int, terms: WinTerms | None = None) -> None:
    """A win: record it (see _record_win) and hand the turn to the winner."""
    _record_win(session, game, winner_id=winner_id, terms=terms)
    turns.set_next_starter(session, winner_id, reason=f"won game {game.id}")


def _record_win(
    session: Session, game: Game, *, winner_id: int, terms: WinTerms | None = None
) -> None:
    """The win itself — status, winner, ended_at, wins counter — without
    touching whose turn it is next (a re-finish that keeps the turn uses
    this directly)."""
    terms = terms or WinTerms()
    game.status = GameStatus.WON
    game.winner_id = winner_id
    # An admin re-finish (services/game/refinish.py) keeps the original
    # ending time, so the hard-mode discount streak's order doesn't move.
    if game.ended_at is None:
        game.ended_at = datetime.now(UTC)

    facts = win_facts(session, game, winner_id, terms.how)
    winner = players.get_or_create_player(session, winner_id)
    winner.wins += terms.award

    winner_text = players.describe_player_id(session, winner_id)
    if game.hard_mode:
        where = f"hard-mode turn {game.hard_mode_turn}"
    elif game.current_stage is not None:
        where = stage_label(game.current_stage)
    else:
        where = "unknown stage"
    logger.info(
        "won by {winner} at {stage}",
        winner=winner_text,
        winner_id=winner_id,
        stage=where,
        game_id=game.id,
    )
    events.emit(
        session,
        EventType.GAME_WON,
        events.Involved(actor_id=winner_id, subject_id=game.starter_id, game_id=game.id),
        **facts,
    )


def _emit_detached(
    game: Game, event_type: EventType, involved: events.Involved, **data: Any
) -> None:
    """emit() for the two state functions that only take a game."""
    session = object_session(game)
    if session is None:
        logger.debug("game not in a session — {event_type} not logged", event_type=event_type.value)
        return
    events.emit(session, event_type, involved, **data)


def force_unsolved(game: Game, *, cause: str) -> None:
    """Ends a game unsolved — used both by record_guess's stage-exhaustion
    path and by the timeout job callback. See MECHANICS.md's "Ending
    unsolved" section. `cause` is for the log."""
    game.status = GameStatus.UNSOLVED
    game.ended_at = datetime.now(UTC)
    logger.info("ended UNSOLVED ({cause})", cause=cause, game_id=game.id)
    session = object_session(game)
    guessed = 0 if session is None else len(guesses.guessers(session, game.id))
    _emit_detached(
        game,
        EventType.GAME_UNSOLVED,
        events.Involved(subject_id=game.starter_id, game_id=game.id),
        cause=cause,
        hard_mode=game.hard_mode,
        distinct_guessers=guessed,
    )


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
