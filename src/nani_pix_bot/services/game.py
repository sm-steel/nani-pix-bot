"""The game state machine — the only module that mutates a Game row. See
MECHANICS.md for the rules this implements."""

import enum
from datetime import UTC, datetime, timedelta

from loguru import logger
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from nani_pix_bot.models.enums import GameStatus, PixelStage
from nani_pix_bot.models.game import Game
from nani_pix_bot.models.player import Player
from nani_pix_bot.models.turn_state import TurnState
from nani_pix_bot.services import matching
from nani_pix_bot.services.anilist import AniListResult
from nani_pix_bot.services.shikimori import ShikimoriResult

TURN_STATE_ID = 1

# Blockiest to clearest — see MECHANICS.md's "Pixelation stages" table.
STAGE_ORDER = [PixelStage.X10, PixelStage.X8, PixelStage.X5, PixelStage.X2]
GUESSES_PER_STAGE = 5

# Absolute from game start, not reset by activity — see MECHANICS.md's
# "Timeout" section.
TIMEOUT_DURATION = timedelta(days=2)

# Absolute from SETUP creation, not extended by activity within setup —
# see MECHANICS.md's "Starting a game" section.
SETUP_ABANDON_DELAY = timedelta(hours=1)

# Absolute from a turn being designated to a real user (a win, or
# /skip @user) — see MECHANICS.md's "Turn handoff" section.
TURN_REMINDER_DELAY = timedelta(minutes=15)
TURN_EXPIRY_DELAY = timedelta(hours=12)


class GuessOutcome(enum.Enum):
    """What a /guess attempt did to the game — tells the command layer
    which reply/image to send. Not persisted."""

    WON = "won"
    WRONG = "wrong"
    STAGE_ADVANCED = "stage_advanced"
    UNSOLVED = "unsolved"


def get_or_create_player(
    session: Session, telegram_user_id: int, *, username: str | None = None
) -> Player:
    """Look up a player, creating the row if this is their first time. On
    an existing row, opportunistically refreshes `username`."""
    player = session.get(Player, telegram_user_id)
    if player is None:
        player = Player(telegram_user_id=telegram_user_id, username=username)
        session.add(player)
    elif username is not None:
        player.username = username
    return player


def get_turn_state(session: Session) -> TurnState | None:
    return session.get(TurnState, TURN_STATE_ID)


def _get_or_create_turn_state(session: Session) -> TurnState:
    turn_state = get_turn_state(session)
    if turn_state is None:
        turn_state = TurnState(id=TURN_STATE_ID)
        session.add(turn_state)
    return turn_state


def set_next_starter(session: Session, user_id: int | None) -> TurnState:
    """Implements /skip and winning — see MECHANICS.md's "Turn handoff"
    section. `None` opens the turn to anyone and cancels the win-turn
    reminder/expiry timers; a real user (re)schedules both, absolute
    from now. Returns the row so the command layer can schedule/cancel
    the actual JobQueue jobs (this module stays Telegram-agnostic)."""
    turn_state = _get_or_create_turn_state(session)
    turn_state.next_starter_id = user_id
    if user_id is None:
        turn_state.reminder_at = None
        turn_state.expiry_at = None
        logger.info("Turn opened — anyone may start the next game")
    else:
        now = datetime.now(UTC)
        turn_state.reminder_at = now + TURN_REMINDER_DELAY
        turn_state.expiry_at = now + TURN_EXPIRY_DELAY
        logger.info("Turn designated to player {}", user_id)
    return turn_state


def clear_turn_timers(session: Session) -> None:
    """Cancels the win-turn reminder/expiry deadlines without touching
    `next_starter_id` — used when the designated player actually starts
    their game, so a stale reminder doesn't fire after they've already
    acted."""
    turn_state = _get_or_create_turn_state(session)
    turn_state.reminder_at = None
    turn_state.expiry_at = None


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
    turn_state = get_turn_state(session)
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
    stage_result): move to the X10 stage and open the turn (the
    designated starter's turn is now consumed)."""
    game.status = GameStatus.ACTIVE
    game.current_stage = PixelStage.X10
    game.wrong_guess_count = 0
    game.scheduled_end_at = datetime.now(UTC) + TIMEOUT_DURATION

    turn_state = _get_or_create_turn_state(session)
    turn_state.next_starter_id = None
    logger.info("Game {} activated (source={})", game.id, game.source)


def seconds_until(deadline: datetime | None) -> float:
    """Seconds from now until `deadline`, clamped at 0 for an already-
    overdue deadline (or if there's no deadline at all). Normalizes naive
    datetimes (as DATETIME columns round-trip from the DB) to UTC before
    comparing — shared by the game timeout, setup-abandon, and win-turn
    reminder/expiry timers."""
    if deadline is None:
        return 0.0
    if deadline.tzinfo is None:
        deadline = deadline.replace(tzinfo=UTC)
    return max((deadline - datetime.now(UTC)).total_seconds(), 0.0)


def seconds_until_timeout(game: Game) -> float:
    """Seconds from now until `game.scheduled_end_at` — see seconds_until()."""
    return seconds_until(game.scheduled_end_at)


def timeout_job_name(game_id: int) -> str:
    """Deterministic JobQueue job name for a game's timeout — lets the
    command layer look up and cancel a pending job (e.g. on a win) or
    re-arm it on startup without storing anything extra on the row."""
    return f"game-timeout-{game_id}"


def setup_abandon_job_name(game_id: int) -> str:
    """Deterministic JobQueue job name for a SETUP game's abandon
    timer — mirrors timeout_job_name()."""
    return f"setup-abandon-{game_id}"


def record_guess(session: Session, game: Game, *, guesser_id: int, guess_text: str) -> GuessOutcome:
    """Apply one /guess attempt to an ACTIVE game — see MECHANICS.md's
    "Guess matching" and "Pixelation stages" sections."""
    if game.current_stage is None:
        msg = f"record_guess called on game {game.id} with no current_stage (not ACTIVE?)"
        logger.warning(msg)
        raise ValueError(msg)

    game.total_guess_count += 1

    candidates = [
        game.title_romaji,
        game.title_english,
        game.title_native,
        game.title_russian,
        *(game.synonyms or []),
    ]
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
    if game.wrong_guess_count < GUESSES_PER_STAGE:
        logger.debug(
            "Game {}: wrong guess {}/{} at stage {}",
            game.id,
            game.wrong_guess_count,
            GUESSES_PER_STAGE,
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


def find_player_by_username(session: Session, username: str) -> Player | None:
    """Case-insensitive lookup by the opportunistically-cached username —
    used by /correct, which takes a plain @username rather than a reply."""
    stmt = select(Player).where(func.lower(Player.username) == username.lower())
    return session.scalars(stmt).first()


def force_win(session: Session, game: Game, *, winner_id: int) -> None:
    """The author-override path (/correct) — identical end state to an
    automatic match in record_guess, just triggered without one."""
    _win(session, game, winner_id=winner_id)


def _win(session: Session, game: Game, *, winner_id: int) -> None:
    game.status = GameStatus.WON
    game.winner_id = winner_id

    winner = get_or_create_player(session, winner_id)
    winner.wins += 1

    set_next_starter(session, winner_id)
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
