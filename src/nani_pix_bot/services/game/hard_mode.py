"""HARD MODE rules — the bot-autostarted ruleset alternative to state.py's
normal five-PixelStage progression. A hard-mode game plays a single fixed
screenshot pair (Game.hard_mode_image_a/_b) across HARD_MODE_TURN_COUNT
turns instead of pixelation stages: each turn has its own wrong-guess
budget (HARD_MODE_WRONG_GUESS_LIMIT) rather than a per-PixelStage one,
and a turn exhausting its budget advances to the next turn or, on the
last turn, ends the game unsolved — mirroring state.py's record_guess/
advance_stage shape but against Game.hard_mode_turn instead of
Game.current_stage. A related but distinct concern from state.py's core
state machine, same relationship turns.py already has to it (see
state.py's own module docstring).

Imports state.py at module level — this is the primary direction of the
dependency (state._win/state.force_unsolved are the shared win/unsolved
paths this module reuses rather than duplicating). The reverse import
(state.py needing this module, to dispatch a hard-mode game's /guess) is
a local, function-body-only import instead, to avoid turning that into a
genuine circular import — see state.py's record_guess/force_win for that
half of this precedent."""

from typing import NamedTuple

from loguru import logger
from sqlalchemy import select
from sqlalchemy.orm import Session

from nani_pix_bot.models.enums import GameStatus, WinMethod
from nani_pix_bot.models.game import Game
from nani_pix_bot.services.game import guesses, state
from nani_pix_bot.services.game.state import GuessOutcome

# HARD MODE clue discount (issue #254): each consecutive failed hard-mode game
# raises the next one's discount by a step, up to the cap; a solved one
# (by guess, vote or admin re-finish) resets it to the start.
HARD_MODE_DISCOUNT_START = 20
HARD_MODE_DISCOUNT_STEP = 20
HARD_MODE_DISCOUNT_MAX = 80
# Enough recent games to reach the cap; older ones can't change the answer.
_STREAK_LOOKBACK = (
    HARD_MODE_DISCOUNT_MAX - HARD_MODE_DISCOUNT_START
) // HARD_MODE_DISCOUNT_STEP + 1
# Fixed: hard mode always plays exactly two turns against the same
# screenshot pair — no admin config, no third value (see Game.hard_mode_turn's
# docstring in models/game.py).
HARD_MODE_TURN_COUNT = 2
# A turn's own wrong-guess budget before it advances/ends — unlike
# stage_config.py's per-PixelStage limits, this isn't admin-configurable;
# hard mode is deliberately unforgiving throughout.
HARD_MODE_WRONG_GUESS_LIMIT = 1
# Player.wins credited on a hard-mode win, vs. the normal +1 a PixelStage
# win awards via state.py's _win() default.
HARD_MODE_WIN_AWARD = 2
# target_width per turn. Turn 1 (64px) is pixelated identically to a
# normal game's STAGE_1 — not harder — and turn 2 (160px) is comfortably
# revealing. HARD MODE's difficulty comes from HARD_MODE_WRONG_GUESS_LIMIT
# and HARD_MODE_TURN_COUNT (one guess per turn, only two turns total), not
# from a harsher pixelation width than a normal game starts at.
HARD_MODE_TURN_WIDTHS: dict[int, int] = {1: 64, 2: 160}


class HardModeTurnProgress(NamedTuple):
    """Where an ACTIVE hard-mode game stands within its two turns — the
    hard-mode analogue of state.py's StageProgress; see that class's
    docstring for why both `remaining` and `limit` are reported."""

    number: int
    total: int
    remaining: int
    limit: int


def _current_turn(game: Game) -> int:
    """The 1-or-2 turn number a hard-mode game is on — narrows
    Game.hard_mode_turn's `int | None` column type down to `int` for
    every function below. `None` here would mean this function was
    called on a game that isn't actually in an active hard-mode round
    (state.py's record_guess/activate_game only ever reach this module
    after confirming game.hard_mode is True and activation has run), so
    this is a genuine internal invariant rather than user input to
    validate — same reasoning as state.py's advance_stage RuntimeError
    for a missing current_stage."""
    if game.hard_mode_turn is None:
        msg = f"hard-mode game {game.id} has no hard_mode_turn set"
        logger.error(msg)
        raise RuntimeError(msg)
    return game.hard_mode_turn


def _turn_label(game: Game) -> str:
    """`hard-mode turn 1/2`: the hard-mode counterpart of state.stage_label
    in log lines."""
    return f"hard-mode turn {game.hard_mode_turn}/{HARD_MODE_TURN_COUNT}"


def record_hard_mode_guess(
    session: Session, game: Game, *, guesser_id: int, guess_text: str
) -> GuessOutcome:
    """Apply one /guess attempt to an ACTIVE hard-mode game — the
    hard-mode analogue of state.py's record_guess, mirrored in shape
    against HARD_MODE_TURN_COUNT/HARD_MODE_WRONG_GUESS_LIMIT instead of
    STAGE_ORDER/stage_config."""
    game.total_guess_count += 1

    correct = state.is_correct_guess(game, guess_text)
    reveal = None if correct else state.partial_reveal_text(session, game, guess_text)
    if reveal is not None:
        logger.info("partial match revealed {reveal!r}", reveal=reveal, game_id=game.id)
    guesses.log_guess(
        session,
        game,
        guesses.GuessRecord(
            player_id=guesser_id,
            text=guess_text,
            stage=_current_turn(game),
            correct=correct,
            partial_reveal=reveal,
            score=None if correct else state.guess_score(game, guess_text),
        ),
    )
    if correct:
        logger.info(
            "guessed {guess!r} — CORRECT at {stage}",
            guess=guess_text,
            stage=_turn_label(game),
            game_id=game.id,
        )
        state._win(
            session,
            game,
            winner_id=guesser_id,
            terms=state.WinTerms(award=HARD_MODE_WIN_AWARD, how=WinMethod.GUESS),
        )
        return GuessOutcome.WON

    game.wrong_guess_count += 1
    logger.info(
        "guessed {guess!r} — wrong at {stage} ({wrong}/{limit})",
        guess=guess_text,
        stage=_turn_label(game),
        wrong=game.wrong_guess_count,
        limit=HARD_MODE_WRONG_GUESS_LIMIT,
        game_id=game.id,
    )
    if game.wrong_guess_count < HARD_MODE_WRONG_GUESS_LIMIT:
        return GuessOutcome.WRONG

    game.wrong_guess_count = 0
    turn = _current_turn(game)
    if turn < HARD_MODE_TURN_COUNT:
        game.hard_mode_turn = turn + 1
        logger.info(
            "advanced to {stage} ({reason})",
            stage=_turn_label(game),
            reason="wrong-guess limit reached",
            game_id=game.id,
        )
        return GuessOutcome.TURN_ADVANCED

    # Local import — vote.py imports this module at module level (see its docstring).
    from nani_pix_bot.services.game import vote

    return vote.end_hard_mode_without_winner(
        session, game, cause="final hard-mode turn exhausted — wrong-guess limit reached"
    )


def hard_mode_turn_progress(game: Game) -> HardModeTurnProgress:
    """Current hard-mode turn number/total and the wrong-guess budget
    left at it, for a still-ACTIVE hard-mode game — the hard-mode
    analogue of state.py's stage_progress()."""
    turn = _current_turn(game)
    remaining = HARD_MODE_WRONG_GUESS_LIMIT - game.wrong_guess_count
    return HardModeTurnProgress(turn, HARD_MODE_TURN_COUNT, remaining, HARD_MODE_WRONG_GUESS_LIMIT)


def has_hard_mode_reveal_images(game: Game) -> bool:
    """Whether both hard-mode screenshots are still present on `game`."""
    return game.hard_mode_image_a is not None and game.hard_mode_image_b is not None


def hard_mode_reveal_images(game: Game) -> tuple[bytes, bytes]:
    """The (a, b) hard-mode screenshot pair, once both are confirmed
    present — raises if called before both are set; every real caller
    checks has_hard_mode_reveal_images() first, so this is defensive,
    not a path anything is expected to hit."""
    if game.hard_mode_image_a is None or game.hard_mode_image_b is None:
        msg = f"hard_mode_reveal_images called on game {game.id} with a missing image"
        logger.error(msg)
        raise RuntimeError(msg)
    return game.hard_mode_image_a, game.hard_mode_image_b


def clear_hard_mode_images(game: Game) -> None:
    """Drop both stored hard-mode image pair's bytes once a reveal
    message is confirmed sent — the hard-mode analogue of state.py's
    clear_original_screenshot()."""
    game.hard_mode_image_a = None
    game.hard_mode_image_b = None


def hard_mode_turn_width(game: Game) -> int:
    """The target_width to pixelate at for `game`'s current hard-mode
    turn — see HARD_MODE_TURN_WIDTHS."""
    return HARD_MODE_TURN_WIDTHS[_current_turn(game)]


def discount_for_streak(streak: int) -> int:
    return min(HARD_MODE_DISCOUNT_START + HARD_MODE_DISCOUNT_STEP * streak, HARD_MODE_DISCOUNT_MAX)


def failed_hard_mode_streak(session: Session) -> int:
    """How many of the most recent ended hard-mode games, in a row, ended
    UNSOLVED. Games with no ended_at (pre-#239) are ignored."""
    stmt = (
        select(Game.status)
        .where(
            Game.hard_mode.is_(True),
            Game.status.in_([GameStatus.WON, GameStatus.UNSOLVED]),
            Game.ended_at.is_not(None),
        )
        .order_by(Game.ended_at.desc(), Game.id.desc())
        .limit(_STREAK_LOOKBACK)
    )
    streak = 0
    for status in session.scalars(stmt):
        if status != GameStatus.UNSOLVED:
            break
        streak += 1
    return streak


def next_clue_discount(session: Session) -> int:
    return discount_for_streak(failed_hard_mode_streak(session))
