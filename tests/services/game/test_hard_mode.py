from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from nani_pix_bot.models.enums import GameStatus
from nani_pix_bot.models.game import Game
from nani_pix_bot.models.game_guess import GameGuess
from nani_pix_bot.models.player import Player
from nani_pix_bot.services import game as game_service
from nani_pix_bot.services.game import hard_mode


def _hard_mode_game(
    session: Session,
    *,
    turn: int = 1,
    wrong_guess_count: int = 0,
    total_guess_count: int = 0,
    hard_mode_image_a: bytes | None = None,
    hard_mode_image_b: bytes | None = None,
) -> Game:
    session.add(Player(telegram_user_id=1))
    session.commit()
    game = Game(
        starter_id=1,
        status=GameStatus.ACTIVE,
        hard_mode=True,
        hard_mode_turn=turn,
        wrong_guess_count=wrong_guess_count,
        total_guess_count=total_guess_count,
        hard_mode_image_a=hard_mode_image_a,
        hard_mode_image_b=hard_mode_image_b,
        anilist_id=99,
        title_romaji="Sousou no Frieren",
        title_english="Frieren: Beyond Journey's End",
        synonyms=["Frieren"],
    )
    session.add(game)
    session.commit()
    return game


def test_record_hard_mode_guess_correct_on_turn_1_wins_with_double_award(session: Session) -> None:
    game = _hard_mode_game(session, turn=1)
    session.add(Player(telegram_user_id=2))
    session.commit()

    outcome = game_service.record_hard_mode_guess(session, game, guesser_id=2, guess_text="frieren")
    session.commit()

    assert outcome is game_service.GuessOutcome.WON
    assert game.status == GameStatus.WON

    winner = session.get(Player, 2)
    assert winner is not None
    assert winner.wins == hard_mode.HARD_MODE_WIN_AWARD
    assert winner.wins == 2


def test_record_hard_mode_guess_wrong_on_turn_1_advances_to_turn_2(session: Session) -> None:
    game = _hard_mode_game(session, turn=1)

    outcome = game_service.record_hard_mode_guess(
        session, game, guesser_id=1, guess_text="attack on titan"
    )
    session.commit()

    assert outcome is game_service.GuessOutcome.TURN_ADVANCED
    assert game.hard_mode_turn == 2
    assert game.wrong_guess_count == 0
    assert game.status == GameStatus.ACTIVE


def test_record_hard_mode_guess_correct_on_turn_2_wins_with_double_award(session: Session) -> None:
    game = _hard_mode_game(session, turn=2)
    session.add(Player(telegram_user_id=2))
    session.commit()

    outcome = game_service.record_hard_mode_guess(session, game, guesser_id=2, guess_text="frieren")
    session.commit()

    assert outcome is game_service.GuessOutcome.WON
    winner = session.get(Player, 2)
    assert winner is not None
    assert winner.wins == 2


def test_record_hard_mode_guess_wrong_on_turn_2_opens_a_vote(session: Session) -> None:
    game = _hard_mode_game(session, turn=2)

    outcome = game_service.record_hard_mode_guess(
        session, game, guesser_id=1, guess_text="attack on titan"
    )
    session.commit()

    assert outcome is game_service.GuessOutcome.VOTE_OPENED
    assert game.status == GameStatus.VOTING


def test_final_turn_wrong_guess_opens_a_vote(session: Session) -> None:
    game = _hard_mode_game(session, turn=2)
    session.add(Player(telegram_user_id=2))
    session.flush()
    outcome = hard_mode.record_hard_mode_guess(session, game, guesser_id=2, guess_text="nope")
    assert outcome is game_service.GuessOutcome.VOTE_OPENED
    assert game.status is GameStatus.VOTING


def test_record_guess_leaves_the_turn_alone_when_a_hard_mode_vote_opens(session: Session) -> None:
    # VOTE_OPENED is not UNSOLVED: the vote still holds the round, so
    # record_guess must not arm the idle-autostart backstop for it.
    game = _hard_mode_game(session, turn=2)

    outcome = game_service.record_guess(session, game, guesser_id=2, guess_text="wrong answer")

    assert outcome is game_service.GuessOutcome.VOTE_OPENED
    turn_state = game_service.get_turn_state(session)
    assert turn_state is None or turn_state.autostart_deadline_at is None


def test_record_hard_mode_guess_wrong_branch_is_reachable_with_a_higher_limit(
    session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    # With the real HARD_MODE_WRONG_GUESS_LIMIT (1), a turn's very first
    # wrong guess always triggers advance/end, so GuessOutcome.WRONG can
    # never actually be returned in practice — but the branch must still
    # exist and behave correctly. Monkeypatching the limit up is the only
    # way to exercise it, mirroring this codebase's existing precedent
    # for patching a module-level constant/function directly (e.g.
    # tests/jobs/test_timers.py's monkeypatch.setattr("...pixelate...")).
    monkeypatch.setattr(hard_mode, "HARD_MODE_WRONG_GUESS_LIMIT", 2)
    game = _hard_mode_game(session, turn=1, wrong_guess_count=0)

    outcome = game_service.record_hard_mode_guess(
        session, game, guesser_id=1, guess_text="attack on titan"
    )
    session.commit()

    assert outcome is game_service.GuessOutcome.WRONG
    assert game.hard_mode_turn == 1
    assert game.wrong_guess_count == 1
    assert game.status == GameStatus.ACTIVE


def test_record_hard_mode_guess_total_guess_count_increments_on_every_attempt(
    session: Session,
) -> None:
    game = _hard_mode_game(session, turn=1, total_guess_count=3)

    game_service.record_hard_mode_guess(session, game, guesser_id=1, guess_text="wrong")
    session.commit()

    assert game.total_guess_count == 4


def test_record_hard_mode_guess_total_guess_count_increments_on_a_win(session: Session) -> None:
    game = _hard_mode_game(session, turn=1, total_guess_count=3)
    session.add(Player(telegram_user_id=2))
    session.commit()

    game_service.record_hard_mode_guess(session, game, guesser_id=2, guess_text="frieren")
    session.commit()

    assert game.total_guess_count == 4


def test_hard_mode_turn_progress_reports_turn_1(session: Session) -> None:
    game = _hard_mode_game(session, turn=1)

    progress = game_service.hard_mode_turn_progress(game)

    assert progress.number == 1
    assert progress.total == 2
    assert progress.remaining == 1
    assert progress.limit == 1


def test_hard_mode_turn_progress_reports_turn_2(session: Session) -> None:
    game = _hard_mode_game(session, turn=2)

    progress = game_service.hard_mode_turn_progress(game)

    assert progress.number == 2
    assert progress.total == 2
    assert progress.remaining == 1
    assert progress.limit == 1


def test_has_hard_mode_reveal_images_is_false_when_both_unset(session: Session) -> None:
    game = _hard_mode_game(session)

    assert game_service.has_hard_mode_reveal_images(game) is False


def test_has_hard_mode_reveal_images_is_false_when_only_one_set(session: Session) -> None:
    game = _hard_mode_game(session, hard_mode_image_a=b"a")

    assert game_service.has_hard_mode_reveal_images(game) is False


def test_has_hard_mode_reveal_images_is_true_when_both_set(session: Session) -> None:
    game = _hard_mode_game(session, hard_mode_image_a=b"a", hard_mode_image_b=b"b")

    assert game_service.has_hard_mode_reveal_images(game) is True


def test_hard_mode_reveal_images_returns_both_when_set(session: Session) -> None:
    game = _hard_mode_game(session, hard_mode_image_a=b"a", hard_mode_image_b=b"b")

    assert game_service.hard_mode_reveal_images(game) == (b"a", b"b")


def test_hard_mode_reveal_images_raises_when_a_is_unset(session: Session) -> None:
    game = _hard_mode_game(session, hard_mode_image_b=b"b")

    with pytest.raises(RuntimeError):
        game_service.hard_mode_reveal_images(game)


def test_hard_mode_reveal_images_raises_when_b_is_unset(session: Session) -> None:
    game = _hard_mode_game(session, hard_mode_image_a=b"a")

    with pytest.raises(RuntimeError):
        game_service.hard_mode_reveal_images(game)


def test_clear_hard_mode_images_nulls_both_columns(session: Session) -> None:
    game = _hard_mode_game(session, hard_mode_image_a=b"a", hard_mode_image_b=b"b")

    game_service.clear_hard_mode_images(game)

    assert game.hard_mode_image_a is None
    assert game.hard_mode_image_b is None


def test_clear_hard_mode_images_is_a_no_op_when_already_unset(session: Session) -> None:
    game = _hard_mode_game(session)

    game_service.clear_hard_mode_images(game)

    assert game.hard_mode_image_a is None
    assert game.hard_mode_image_b is None


def test_hard_mode_turn_width_for_turn_1(session: Session) -> None:
    game = _hard_mode_game(session, turn=1)

    assert game_service.hard_mode_turn_width(game) == hard_mode.HARD_MODE_TURN_WIDTHS[1]


def test_hard_mode_turn_width_for_turn_2(session: Session) -> None:
    game = _hard_mode_game(session, turn=2)

    assert game_service.hard_mode_turn_width(game) == hard_mode.HARD_MODE_TURN_WIDTHS[2]


def test_record_hard_mode_guess_logs_a_wrong_guess_at_info(
    session: Session, records: list[tuple[str, str]]
) -> None:
    game = _hard_mode_game(session, turn=1)
    session.add(Player(telegram_user_id=2, username="bob"))
    session.commit()

    game_service.record_hard_mode_guess(session, game, guesser_id=2, guess_text="naruto")

    assert (
        "INFO",
        "guessed 'naruto' — wrong at hard-mode turn 1/2 (1/1)",
    ) in records


def test_record_hard_mode_guess_logs_a_correct_guess_at_info(
    session: Session, records: list[tuple[str, str]]
) -> None:
    game = _hard_mode_game(session, turn=2)
    session.add(Player(telegram_user_id=2, username="bob"))
    session.commit()

    game_service.record_hard_mode_guess(session, game, guesser_id=2, guess_text="frieren")

    assert (
        "INFO",
        "guessed 'frieren' — CORRECT at hard-mode turn 2/2",
    ) in records


def test_hard_mode_guess_is_logged_with_its_turn(session: Session) -> None:
    game = _hard_mode_game(session, turn=1)
    session.add(Player(telegram_user_id=2))
    session.flush()
    hard_mode.record_hard_mode_guess(session, game, guesser_id=2, guess_text="Frieren")
    row = session.scalars(
        select(GameGuess).where(GameGuess.game_id == game.id).order_by(GameGuess.id.desc())
    ).first()
    assert row is not None
    assert (row.stage, row.correct) == (1, True)


@pytest.mark.parametrize(("streak", "percent"), [(0, 20), (1, 40), (2, 60), (3, 80), (9, 80)])
def test_discount_for_streak(streak: int, percent: int) -> None:
    assert hard_mode.discount_for_streak(streak) == percent


def _ended(
    session: Session, status: GameStatus, minutes_ago: int, *, hard: bool = True, ended: bool = True
) -> None:
    session.add(
        Game(
            starter_id=1,
            status=status,
            hard_mode=hard,
            ended_at=datetime.now(UTC) - timedelta(minutes=minutes_ago) if ended else None,
        )
    )
    session.flush()


def test_streak_counts_consecutive_unsolved_hard_mode_games_newest_first(session: Session) -> None:
    session.add(Player(telegram_user_id=1))
    session.flush()
    _ended(session, GameStatus.UNSOLVED, 50)
    _ended(session, GameStatus.WON, 40)
    _ended(session, GameStatus.UNSOLVED, 30)
    _ended(session, GameStatus.UNSOLVED, 20, hard=False)  # normal games don't count
    _ended(session, GameStatus.UNSOLVED, 10)
    _ended(session, GameStatus.UNSOLVED, 5, ended=False)  # legacy rows with no ended_at are ignored

    assert hard_mode.failed_hard_mode_streak(session) == 2
    assert hard_mode.next_clue_discount(session) == 60


def test_no_history_starts_at_twenty(session: Session) -> None:
    assert hard_mode.next_clue_discount(session) == 20
