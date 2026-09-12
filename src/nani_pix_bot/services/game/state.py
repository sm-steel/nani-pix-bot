"""The core Game-row state machine — the only module that mutates a
Game row. See MECHANICS.md for the rules this implements. TurnState
bookkeeping (a related but distinct concern) lives in turns.py."""

import enum
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from loguru import logger
from sqlalchemy import select
from sqlalchemy.orm import Session

from nani_pix_bot.models.enums import GameStatus, PixelStage
from nani_pix_bot.models.game import Game
from nani_pix_bot.services import matching, players
from nani_pix_bot.services.game import turns
from nani_pix_bot.services.search.anilist import AniListResult
from nani_pix_bot.services.search.shikimori import ShikimoriResult
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


def create_setup_game(session: Session, *, starter_id: int, original_file_id: str) -> Game:
    game = Game(
        starter_id=starter_id,
        original_file_id=original_file_id,
        status=GameStatus.SETUP,
        setup_deadline=datetime.now(UTC) + SETUP_ABANDON_DELAY,
    )
    session.add(game)
    session.flush()  # populate game.id for the caller without a full commit
    logger.info("Game {} created (SETUP) by starter {}", game.id, starter_id)
    return game


def stage_result(game: Game, result: AniListResult | ShikimoriResult, *, source: str) -> None:
    """Assign a picked search result's title/synonyms onto a still-SETUP
    game — doesn't post anything or change status. This is the shared
    landing spot for every identification method (AniList, Shikimori,
    and eventually manual entry); the confirmation-screen ticket (#19)
    is what will show a preview between this and activate_game()."""
    game.title_romaji = result.title_romaji
    game.title_english = result.title_english
    game.synonyms = result.synonyms
    game.source = source
    if isinstance(result, AniListResult):
        game.anilist_id = result.anilist_id
        game.title_native = result.title_native
    elif isinstance(result, ShikimoriResult):
        game.title_russian = result.title_russian


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
    limit = stage_config.get_stage_config(session)[game.current_stage].wrong_guess_limit
    if game.wrong_guess_count < limit:
        logger.debug(
            "Game {}: wrong guess {}/{} at stage {}",
            game.id,
            game.wrong_guess_count,
            limit,
            game.current_stage,
        )
        return GuessOutcome.WRONG

    game.wrong_guess_count = 0
    next_index = STAGE_ORDER.index(game.current_stage) + 1
    if next_index >= len(STAGE_ORDER):
        force_unsolved(game)
        return GuessOutcome.UNSOLVED

    game.current_stage = STAGE_ORDER[next_index]
    logger.info("Game {} advanced to stage {}", game.id, game.current_stage)
    return GuessOutcome.STAGE_ADVANCED


def stage_progress(session: Session, game: Game) -> tuple[int, int, int]:
    """(1-indexed current stage number, total stage count, wrong guesses
    remaining before the next stage) for a still-ACTIVE game — feeds the
    /guess wrong-feedback message."""
    if game.current_stage is None:
        msg = f"stage_progress called on game {game.id} with no current_stage (not ACTIVE?)"
        raise ValueError(msg)

    stage_number = STAGE_ORDER.index(game.current_stage) + 1
    limit = stage_config.get_stage_config(session)[game.current_stage].wrong_guess_limit
    remaining = limit - game.wrong_guess_count
    return stage_number, len(STAGE_ORDER), remaining


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


def clear_original_screenshot(game: Game) -> None:
    """Drop the stored Telegram file reference once a reveal message is
    confirmed sent — see MECHANICS.md's "Cleanup" note."""
    game.original_file_id = None
