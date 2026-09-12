from datetime import UTC, datetime

import pytest
from sqlalchemy.orm import Session

from nani_pix_bot.models.enums import GameStatus, PixelStage
from nani_pix_bot.models.game import Game
from nani_pix_bot.models.player import Player
from nani_pix_bot.models.stage_config import StageConfig
from nani_pix_bot.models.turn_state import TurnState
from nani_pix_bot.services import game as game_service
from nani_pix_bot.services.anilist import AniListResult
from nani_pix_bot.services.shikimori import ShikimoriResult

_FRIEREN = AniListResult(
    anilist_id=99,
    title_romaji="Sousou no Frieren",
    title_english="Frieren: Beyond Journey's End",
    title_native="葬送のフリーレン",
    synonyms=["Frieren"],
    year=2023,
)

_FRIEREN_SHIKIMORI = ShikimoriResult(
    shikimori_id=52991,
    title_romaji="Sousou no Frieren",
    title_english="Frieren: Beyond Journey's End",
    title_russian="Провожающая в последний путь Фрирен",
    synonyms=["Frieren at the Funeral"],
)


def test_display_title_prefers_english() -> None:
    game = Game(
        starter_id=1,
        original_file_id="f",
        title_english="Frieren: Beyond Journey's End",
        title_romaji="Sousou no Frieren",
        title_native="葬送のフリーレン",
        title_russian="Провожающая в последний путь Фрирен",
    )

    assert game_service.display_title(game) == "Frieren: Beyond Journey's End"


def test_display_title_falls_back_to_russian_when_only_that_is_set() -> None:
    # A Shikimori-only result has no English/romaji/native title — before
    # consolidating this fallback chain, three of its four copies
    # (commands/guess.py, correct.py, stop.py) skipped straight to "?"
    # here instead of using the Russian title.
    game = Game(
        starter_id=1,
        original_file_id="f",
        title_russian="Провожающая в последний путь Фрирен",
    )

    assert game_service.display_title(game) == "Провожающая в последний путь Фрирен"


def test_display_title_falls_back_to_a_literal_question_mark() -> None:
    game = Game(starter_id=1, original_file_id="f")

    assert game_service.display_title(game) == "?"


def test_can_start_is_true_with_no_game_and_no_turn_state(session: Session) -> None:
    assert game_service.can_start(session, user_id=1) is True


def test_can_start_is_false_when_a_game_is_active(session: Session) -> None:
    session.add(Player(telegram_user_id=1))
    session.commit()
    session.add(Game(starter_id=1, original_file_id="f", status=GameStatus.ACTIVE))
    session.commit()

    assert game_service.can_start(session, user_id=2) is False


def test_can_start_is_false_when_turn_designated_to_someone_else(session: Session) -> None:
    session.add_all([Player(telegram_user_id=1), Player(telegram_user_id=2)])
    session.add(TurnState(id=1, next_starter_id=2))
    session.commit()

    assert game_service.can_start(session, user_id=1) is False


def test_can_start_is_true_for_the_designated_starter(session: Session) -> None:
    session.add(Player(telegram_user_id=2))
    session.add(TurnState(id=1, next_starter_id=2))
    session.commit()

    assert game_service.can_start(session, user_id=2) is True


def test_create_setup_game_persists_a_setup_row(session: Session) -> None:
    session.add(Player(telegram_user_id=1))
    session.commit()

    game = game_service.create_setup_game(session, starter_id=1, original_file_id="file123")
    session.commit()

    fetched = session.get(Game, game.id)
    assert fetched is not None
    assert fetched.status == GameStatus.SETUP
    assert fetched.original_file_id == "file123"
    assert fetched.starter_id == 1


def test_create_setup_game_sets_a_one_hour_setup_deadline(session: Session) -> None:
    session.add(Player(telegram_user_id=1))
    session.commit()
    before = datetime.now(UTC).replace(tzinfo=None)

    game = game_service.create_setup_game(session, starter_id=1, original_file_id="file123")
    session.commit()

    assert game.setup_deadline is not None
    delta_seconds = (game.setup_deadline - before).total_seconds()
    assert delta_seconds == pytest.approx(game_service.SETUP_ABANDON_DELAY.total_seconds(), abs=5)


def test_stage_result_assigns_anilist_fields_without_changing_status(session: Session) -> None:
    session.add(Player(telegram_user_id=1))
    session.commit()
    game = game_service.create_setup_game(session, starter_id=1, original_file_id="file123")
    session.commit()

    game_service.stage_result(game, _FRIEREN, source="anilist")
    session.commit()

    fetched = session.get(Game, game.id)
    assert fetched is not None
    assert fetched.status == GameStatus.SETUP
    assert fetched.anilist_id == 99
    assert fetched.title_romaji == "Sousou no Frieren"
    assert fetched.synonyms == ["Frieren"]
    assert fetched.source == "anilist"
    assert fetched.title_russian is None


def test_stage_result_assigns_shikimori_fields_including_russian_title(session: Session) -> None:
    session.add(Player(telegram_user_id=1))
    session.commit()
    game = game_service.create_setup_game(session, starter_id=1, original_file_id="file123")
    session.commit()

    game_service.stage_result(game, _FRIEREN_SHIKIMORI, source="shikimori")
    session.commit()

    fetched = session.get(Game, game.id)
    assert fetched is not None
    assert fetched.status == GameStatus.SETUP
    assert fetched.anilist_id is None
    assert fetched.title_romaji == "Sousou no Frieren"
    assert fetched.title_russian == "Провожающая в последний путь Фрирен"
    assert fetched.synonyms == ["Frieren at the Funeral"]
    assert fetched.source == "shikimori"


def test_stage_manual_entry_assigns_the_typed_title_and_synonyms(session: Session) -> None:
    session.add(Player(telegram_user_id=1))
    session.commit()
    game = game_service.create_setup_game(session, starter_id=1, original_file_id="file123")
    session.commit()

    game_service.stage_manual_entry(
        game, title="Sousou no Frieren", synonyms=["Frieren", "Frieren at the Funeral"]
    )
    session.commit()

    fetched = session.get(Game, game.id)
    assert fetched is not None
    assert fetched.status == GameStatus.SETUP
    assert fetched.source == "manual"
    assert fetched.title_english == "Sousou no Frieren"
    assert fetched.synonyms == ["Frieren", "Frieren at the Funeral"]
    assert fetched.anilist_id is None


def test_activate_game_sets_active_state_and_opens_the_turn(session: Session) -> None:
    session.add(Player(telegram_user_id=1))
    session.add(TurnState(id=1, next_starter_id=1))
    session.commit()
    game = game_service.create_setup_game(session, starter_id=1, original_file_id="file123")
    session.commit()

    game_service.stage_result(game, _FRIEREN, source="anilist")
    game_service.activate_game(session, game)
    session.commit()

    fetched = session.get(Game, game.id)
    assert fetched is not None
    assert fetched.status == GameStatus.ACTIVE
    assert fetched.current_stage == PixelStage.STAGE_1
    assert fetched.wrong_guess_count == 0
    assert fetched.anilist_id == 99
    assert fetched.synonyms == ["Frieren"]

    turn_state = session.get(TurnState, 1)
    assert turn_state is not None
    assert turn_state.next_starter_id is None


def test_activate_game_creates_turn_state_row_if_missing(session: Session) -> None:
    session.add(Player(telegram_user_id=1))
    session.commit()
    game = game_service.create_setup_game(session, starter_id=1, original_file_id="file123")
    session.commit()

    game_service.stage_result(game, _FRIEREN, source="anilist")
    game_service.activate_game(session, game)
    session.commit()

    turn_state = session.get(TurnState, 1)
    assert turn_state is not None
    assert turn_state.next_starter_id is None


def test_activate_game_schedules_the_timeout_two_days_out(session: Session) -> None:
    session.add(Player(telegram_user_id=1))
    session.commit()
    game = game_service.create_setup_game(session, starter_id=1, original_file_id="file123")
    session.commit()
    before = datetime.now(UTC).replace(tzinfo=None)  # DATETIME columns round-trip as naive UTC

    game_service.stage_result(game, _FRIEREN, source="anilist")
    game_service.activate_game(session, game)
    session.commit()

    assert game.scheduled_end_at is not None
    delta_seconds = (game.scheduled_end_at - before).total_seconds()
    assert delta_seconds == pytest.approx(game_service.TIMEOUT_DURATION.total_seconds(), abs=5)


def _active_game(
    session: Session,
    *,
    stage: PixelStage = PixelStage.STAGE_1,
    wrong_guess_count: int = 0,
    total_guess_count: int = 0,
) -> Game:
    session.add(Player(telegram_user_id=1))
    session.commit()
    game = Game(
        starter_id=1,
        original_file_id="file123",
        status=GameStatus.ACTIVE,
        current_stage=stage,
        wrong_guess_count=wrong_guess_count,
        total_guess_count=total_guess_count,
        anilist_id=99,
        title_romaji="Sousou no Frieren",
        title_english="Frieren: Beyond Journey's End",
        synonyms=["Frieren"],
    )
    session.add(game)
    session.commit()
    return game


def _seed_stage_limit(session: Session, stage: PixelStage, wrong_guess_limit: int) -> None:
    """Seeds an explicit stage_config row so a test's expected threshold
    doesn't depend on whatever services/settings/stage_config.py's current
    DEFAULT_STAGE_CONFIG happens to be (which gets retuned often)."""
    session.add(StageConfig(stage=stage, target_width=100, wrong_guess_limit=wrong_guess_limit))
    session.commit()


def test_record_guess_correct_marks_the_game_won(session: Session) -> None:
    game = _active_game(session)
    session.add(Player(telegram_user_id=2))
    session.commit()

    outcome = game_service.record_guess(session, game, guesser_id=2, guess_text="frieren")
    session.commit()

    assert outcome is game_service.GuessOutcome.WON
    assert game.status == GameStatus.WON
    assert game.winner_id == 2

    winner = session.get(Player, 2)
    assert winner is not None
    assert winner.wins == 1

    turn_state = session.get(TurnState, 1)
    assert turn_state is not None
    assert turn_state.next_starter_id == 2


def test_record_guess_matches_the_russian_title(session: Session) -> None:
    game = _active_game(session)
    game.title_russian = "Провожающая в последний путь Фрирен"
    session.add(Player(telegram_user_id=2))
    session.commit()

    outcome = game_service.record_guess(
        session, game, guesser_id=2, guess_text="Провожающая в последний путь Фрирен"
    )
    session.commit()

    assert outcome is game_service.GuessOutcome.WON


def test_record_guess_wrong_increments_the_counter_without_advancing(session: Session) -> None:
    game = _active_game(session, stage=PixelStage.STAGE_4, wrong_guess_count=3)
    _seed_stage_limit(session, PixelStage.STAGE_4, wrong_guess_limit=5)

    outcome = game_service.record_guess(session, game, guesser_id=1, guess_text="attack on titan")
    session.commit()

    assert outcome is game_service.GuessOutcome.WRONG
    assert game.status == GameStatus.ACTIVE
    assert game.current_stage == PixelStage.STAGE_4
    assert game.wrong_guess_count == 4


def test_record_guess_advances_stage_when_a_stages_threshold_is_reached(session: Session) -> None:
    game = _active_game(session, stage=PixelStage.STAGE_4, wrong_guess_count=4)
    _seed_stage_limit(session, PixelStage.STAGE_4, wrong_guess_limit=5)

    outcome = game_service.record_guess(session, game, guesser_id=1, guess_text="attack on titan")
    session.commit()

    assert outcome is game_service.GuessOutcome.STAGE_ADVANCED
    assert game.status == GameStatus.ACTIVE
    assert game.current_stage == PixelStage.STAGE_5
    assert game.wrong_guess_count == 0


def test_record_guess_ends_unsolved_after_final_stage_exhaustion(session: Session) -> None:
    game = _active_game(session, stage=PixelStage.STAGE_5, wrong_guess_count=7)
    _seed_stage_limit(session, PixelStage.STAGE_5, wrong_guess_limit=8)

    outcome = game_service.record_guess(session, game, guesser_id=1, guess_text="attack on titan")
    session.commit()

    assert outcome is game_service.GuessOutcome.UNSOLVED
    assert game.status == GameStatus.UNSOLVED


def test_record_guess_stage_1_advances_immediately_on_first_wrong_guess(session: Session) -> None:
    game = _active_game(session, stage=PixelStage.STAGE_1, wrong_guess_count=0)
    _seed_stage_limit(session, PixelStage.STAGE_1, wrong_guess_limit=1)

    outcome = game_service.record_guess(session, game, guesser_id=1, guess_text="attack on titan")
    session.commit()

    assert outcome is game_service.GuessOutcome.STAGE_ADVANCED
    assert game.current_stage == PixelStage.STAGE_2
    assert game.wrong_guess_count == 0


def test_record_guess_stage_2_advances_immediately_on_first_wrong_guess(session: Session) -> None:
    game = _active_game(session, stage=PixelStage.STAGE_2, wrong_guess_count=0)
    _seed_stage_limit(session, PixelStage.STAGE_2, wrong_guess_limit=1)

    outcome = game_service.record_guess(session, game, guesser_id=1, guess_text="attack on titan")
    session.commit()

    assert outcome is game_service.GuessOutcome.STAGE_ADVANCED
    assert game.current_stage == PixelStage.STAGE_3
    assert game.wrong_guess_count == 0


def test_record_guess_stage_3_stays_below_its_three_guess_threshold(session: Session) -> None:
    game = _active_game(session, stage=PixelStage.STAGE_3, wrong_guess_count=1)
    _seed_stage_limit(session, PixelStage.STAGE_3, wrong_guess_limit=3)

    outcome = game_service.record_guess(session, game, guesser_id=1, guess_text="attack on titan")
    session.commit()

    assert outcome is game_service.GuessOutcome.WRONG
    assert game.current_stage == PixelStage.STAGE_3
    assert game.wrong_guess_count == 2


def test_record_guess_stage_3_advances_on_its_third_wrong_guess(session: Session) -> None:
    game = _active_game(session, stage=PixelStage.STAGE_3, wrong_guess_count=2)
    _seed_stage_limit(session, PixelStage.STAGE_3, wrong_guess_limit=3)

    outcome = game_service.record_guess(session, game, guesser_id=1, guess_text="attack on titan")
    session.commit()

    assert outcome is game_service.GuessOutcome.STAGE_ADVANCED
    assert game.current_stage == PixelStage.STAGE_4
    assert game.wrong_guess_count == 0


def test_clear_original_screenshot_nulls_the_file_id(session: Session) -> None:
    game = _active_game(session)

    game_service.clear_original_screenshot(game)
    session.commit()

    assert game.original_file_id is None


def test_record_guess_rejects_a_game_with_no_current_stage(session: Session) -> None:
    session.add(Player(telegram_user_id=1))
    session.commit()
    game = Game(
        starter_id=1,
        original_file_id="file123",
        status=GameStatus.SETUP,
        current_stage=None,
    )
    session.add(game)
    session.commit()

    with pytest.raises(ValueError, match="current_stage"):
        game_service.record_guess(session, game, guesser_id=1, guess_text="anything")


def test_stage_progress_reports_stage_number_total_and_remaining(session: Session) -> None:
    game = _active_game(session, stage=PixelStage.STAGE_3, wrong_guess_count=1)
    _seed_stage_limit(session, PixelStage.STAGE_3, wrong_guess_limit=3)

    stage_number, total_stages, remaining = game_service.stage_progress(session, game)

    assert stage_number == 3
    assert total_stages == 5
    assert remaining == 2  # STAGE_3's limit is 3, minus 1 wrong guess so far


def test_stage_progress_rejects_a_game_with_no_current_stage(session: Session) -> None:
    session.add(Player(telegram_user_id=1))
    session.commit()
    game = Game(
        starter_id=1,
        original_file_id="file123",
        status=GameStatus.SETUP,
        current_stage=None,
    )
    session.add(game)
    session.commit()

    with pytest.raises(ValueError, match="current_stage"):
        game_service.stage_progress(session, game)


def test_force_win_sets_winner_and_hands_over_the_turn(session: Session) -> None:
    game = _active_game(session)
    session.add(Player(telegram_user_id=2))
    session.commit()

    game_service.force_win(session, game, winner_id=2)
    session.commit()

    assert game.status == GameStatus.WON
    assert game.winner_id == 2

    winner = session.get(Player, 2)
    assert winner is not None
    assert winner.wins == 1

    turn_state = session.get(TurnState, 1)
    assert turn_state is not None
    assert turn_state.next_starter_id == 2


def test_force_unsolved_sets_the_status(session: Session) -> None:
    game = _active_game(session)

    game_service.force_unsolved(game)
    session.commit()

    assert game.status == GameStatus.UNSOLVED


def test_win_schedules_turn_timers_for_the_winner(session: Session) -> None:
    game = _active_game(session)
    session.add(Player(telegram_user_id=2))
    session.commit()

    game_service.record_guess(session, game, guesser_id=2, guess_text="frieren")
    session.commit()

    turn_state = game_service.get_turn_state(session)
    assert turn_state is not None
    assert turn_state.next_starter_id == 2
    assert turn_state.reminder_at is not None
    assert turn_state.expiry_at is not None


def test_active_games_returns_only_active_status_games(session: Session) -> None:
    session.add(Player(telegram_user_id=1))
    session.commit()
    session.add_all(
        [
            Game(starter_id=1, original_file_id="f", status=GameStatus.ACTIVE),
            Game(starter_id=1, original_file_id="f", status=GameStatus.WON),
            Game(starter_id=1, original_file_id="f", status=GameStatus.SETUP),
        ]
    )
    session.commit()

    active = game_service.active_games(session)

    assert len(active) == 1
    assert active[0].status == GameStatus.ACTIVE


def test_get_setup_game_for_starter_finds_the_pending_row(session: Session) -> None:
    session.add(Player(telegram_user_id=1))
    session.commit()
    game = game_service.create_setup_game(session, starter_id=1, original_file_id="file123")
    session.commit()

    found = game_service.get_setup_game_for_starter(session, 1)

    assert found is not None
    assert found.id == game.id


def test_get_setup_game_for_starter_ignores_other_starters(session: Session) -> None:
    session.add_all([Player(telegram_user_id=1), Player(telegram_user_id=2)])
    session.commit()
    game_service.create_setup_game(session, starter_id=1, original_file_id="file123")
    session.commit()

    assert game_service.get_setup_game_for_starter(session, 2) is None


def test_get_setup_game_for_starter_ignores_active_games(session: Session) -> None:
    game = _active_game(session)

    assert game_service.get_setup_game_for_starter(session, game.starter_id) is None


def test_record_guess_increments_total_guess_count_on_win(session: Session) -> None:
    game = _active_game(session)
    session.add(Player(telegram_user_id=2))
    session.commit()

    game_service.record_guess(session, game, guesser_id=2, guess_text="frieren")
    session.commit()

    assert game.total_guess_count == 1


def test_record_guess_increments_total_guess_count_on_wrong_guess(session: Session) -> None:
    game = _active_game(session)

    game_service.record_guess(session, game, guesser_id=1, guess_text="attack on titan")
    session.commit()

    assert game.total_guess_count == 1


def test_record_guess_total_guess_count_survives_a_stage_advance(session: Session) -> None:
    game = _active_game(session, stage=PixelStage.STAGE_4, wrong_guess_count=4, total_guess_count=4)
    _seed_stage_limit(session, PixelStage.STAGE_4, wrong_guess_limit=5)

    game_service.record_guess(session, game, guesser_id=1, guess_text="attack on titan")
    session.commit()

    assert game.wrong_guess_count == 0
    assert game.total_guess_count == 5
