from sqlalchemy import select
from sqlalchemy.orm import Session

from nani_pix_bot.models.enums import EventType, GameStatus, PixelStage, WinMethod
from nani_pix_bot.models.event_log import EventLog
from nani_pix_bot.models.game import Game
from nani_pix_bot.models.player import Player
from nani_pix_bot.models.stage_config import StageConfig
from nani_pix_bot.services import game as game_service

STARTER, ALICE, BOB = 1, 2, 3


def _game(session: Session, **fields) -> Game:
    session.add_all([Player(telegram_user_id=u) for u in (STARTER, ALICE, BOB)])
    session.flush()
    values = {
        "starter_id": STARTER,
        "status": GameStatus.ACTIVE,
        "current_stage": PixelStage.STAGE_1,
        "title_english": "Frieren",
    }
    values.update(fields)
    game = Game(**values)
    session.add(game)
    session.flush()
    return game


def _events(session: Session, event_type: EventType) -> list[EventLog]:
    stmt = select(EventLog).where(EventLog.event_type == event_type).order_by(EventLog.id)
    return list(session.scalars(stmt))


def _limit(session: Session, stage: PixelStage, limit: int) -> None:
    session.add(StageConfig(stage=stage, target_width=100, wrong_guess_limit=limit))
    session.flush()


def test_activation_logs_source_and_mode(session: Session) -> None:
    game = _game(session, status=GameStatus.SETUP, source="tmdb")

    game_service.activate_game(session, game)

    (row,) = _events(session, EventType.GAME_ACTIVATED)
    assert row.actor_id == STARTER
    assert row.data == {"source": "tmdb", "hard_mode": False, "own_screenshot": True}


def test_a_wrong_guess_logs_its_score_and_first_of_game(session: Session) -> None:
    game = _game(session)
    _limit(session, PixelStage.STAGE_1, 3)

    # "frier" vs "frieren": ratio 83.3 — close, but under the 85 match threshold.
    game_service.record_guess(session, game, guesser_id=ALICE, guess_text="frier")

    (row,) = _events(session, EventType.GUESS)
    assert row.actor_id == ALICE
    assert row.data["correct"] is False
    assert row.data["first_of_game"] is True
    assert 80 < row.data["score"] < 85
    assert row.data["stage"] == 1


def test_a_winning_first_guess_logs_game_won_with_its_facts(session: Session) -> None:
    game = _game(session)

    game_service.record_guess(session, game, guesser_id=ALICE, guess_text="frieren")

    (guess,) = _events(session, EventType.GUESS)
    assert guess.data["score"] is None
    (won,) = _events(session, EventType.GAME_WON)
    assert (won.actor_id, won.subject_id, won.game_id) == (ALICE, STARTER, game.id)
    assert won.data["how"] == WinMethod.GUESS
    assert won.data["first_guess"] is True
    assert won.data["stage"] == 1
    assert won.data["distinct_guessers"] == 1
    assert won.data["winner_wrong"] == 0
    assert won.data["seconds"] is None  # activated_at unset in this fixture
    assert won.data["ended_at"]


def test_correct_is_logged_as_its_own_method(session: Session) -> None:
    game = _game(session, total_guess_count=1)

    game_service.force_win(session, game, winner_id=ALICE)

    (won,) = _events(session, EventType.GAME_WON)
    assert won.data["how"] == WinMethod.CORRECT
    assert won.data["first_guess"] is False


def test_a_win_on_the_last_slot_of_stage_five_is_flagged(session: Session) -> None:
    _limit(session, PixelStage.STAGE_5, 3)
    game = _game(session, current_stage=PixelStage.STAGE_5, wrong_guess_count=2)

    game_service.record_guess(session, game, guesser_id=ALICE, guess_text="frieren")

    (won,) = _events(session, EventType.GAME_WON)
    assert won.data["last_slot"] is True


def test_exhausting_the_last_stage_logs_unsolved_and_the_stage_advance_before_it(
    session: Session,
) -> None:
    _limit(session, PixelStage.STAGE_4, 1)
    _limit(session, PixelStage.STAGE_5, 1)
    game = _game(session, current_stage=PixelStage.STAGE_4)

    game_service.record_guess(session, game, guesser_id=ALICE, guess_text="naruto")
    game_service.record_guess(session, game, guesser_id=BOB, guess_text="bleach")

    (advanced,) = _events(session, EventType.STAGE_ADVANCED)
    assert advanced.data == {"from_stage": 4, "to_stage": 5, "reason": "wrong-guess limit reached"}
    (unsolved,) = _events(session, EventType.GAME_UNSOLVED)
    assert unsolved.subject_id == STARTER
    assert unsolved.data["distinct_guessers"] == 2
    assert unsolved.data["hard_mode"] is False


def test_a_new_partial_reveal_is_flagged_once(session: Session) -> None:
    game = _game(session, title_english="Spy x Family Code White")
    _limit(session, PixelStage.STAGE_1, 5)

    game_service.record_guess(session, game, guesser_id=ALICE, guess_text="family plot")
    game_service.record_guess(session, game, guesser_id=BOB, guess_text="family plot")

    first, second = _events(session, EventType.GUESS)
    assert first.data["reveal_new"] is True
    assert second.data["reveal_new"] is False


def test_refinish_logs_a_setwinner_win(session: Session) -> None:
    game = _game(session, status=GameStatus.UNSOLVED)

    game_service.refinish(session, game, winner_id=ALICE)

    (won,) = _events(session, EventType.GAME_WON)
    assert won.data["how"] == WinMethod.SETWINNER
