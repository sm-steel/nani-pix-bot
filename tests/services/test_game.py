from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy.orm import Session

from nani_pix_bot.models.enums import GameStatus, PixelStage
from nani_pix_bot.models.game import Game
from nani_pix_bot.models.player import Player
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


def test_get_or_create_player_creates_a_new_row(session: Session) -> None:
    player = game_service.get_or_create_player(session, 1, username="frieren")
    session.commit()

    fetched = session.get(Player, 1)
    assert fetched is not None
    assert fetched.username == "frieren"
    assert player.telegram_user_id == 1


def test_get_or_create_player_refreshes_username_on_an_existing_row(session: Session) -> None:
    session.add(Player(telegram_user_id=1, username="old", wins=3))
    session.commit()

    player = game_service.get_or_create_player(session, 1, username="new")
    session.commit()

    assert player.wins == 3
    assert player.username == "new"


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
    game = _active_game(session, wrong_guess_count=3)

    outcome = game_service.record_guess(session, game, guesser_id=1, guess_text="attack on titan")
    session.commit()

    assert outcome is game_service.GuessOutcome.WRONG
    assert game.status == GameStatus.ACTIVE
    assert game.current_stage == PixelStage.STAGE_1
    assert game.wrong_guess_count == 4


def test_record_guess_advances_stage_on_the_fifth_wrong_guess(session: Session) -> None:
    game = _active_game(session, stage=PixelStage.STAGE_1, wrong_guess_count=4)

    outcome = game_service.record_guess(session, game, guesser_id=1, guess_text="attack on titan")
    session.commit()

    assert outcome is game_service.GuessOutcome.STAGE_ADVANCED
    assert game.status == GameStatus.ACTIVE
    assert game.current_stage == PixelStage.STAGE_2
    assert game.wrong_guess_count == 0


def test_record_guess_ends_unsolved_after_x2_stage_exhaustion(session: Session) -> None:
    game = _active_game(session, stage=PixelStage.STAGE_5, wrong_guess_count=4)

    outcome = game_service.record_guess(session, game, guesser_id=1, guess_text="attack on titan")
    session.commit()

    assert outcome is game_service.GuessOutcome.UNSOLVED
    assert game.status == GameStatus.UNSOLVED


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


def test_find_player_by_username_finds_a_case_insensitive_match(session: Session) -> None:
    session.add(Player(telegram_user_id=1, username="Frieren"))
    session.commit()

    found = game_service.find_player_by_username(session, "frieren")

    assert found is not None
    assert found.telegram_user_id == 1


def test_find_player_by_username_returns_none_when_unknown(session: Session) -> None:
    assert game_service.find_player_by_username(session, "nobody") is None


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


def test_get_turn_state_returns_none_when_no_row_exists(session: Session) -> None:
    assert game_service.get_turn_state(session) is None


def test_set_next_starter_creates_the_row_if_missing(session: Session) -> None:
    session.add(Player(telegram_user_id=1))
    session.commit()

    game_service.set_next_starter(session, 1)
    session.commit()

    turn_state = session.get(TurnState, 1)
    assert turn_state is not None
    assert turn_state.next_starter_id == 1


def test_set_next_starter_can_open_the_turn(session: Session) -> None:
    session.add(TurnState(id=1, next_starter_id=1))
    session.commit()

    game_service.set_next_starter(session, None)
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


def test_timeout_job_name_is_stable_and_unique_per_game() -> None:
    assert game_service.timeout_job_name(42) == game_service.timeout_job_name(42)
    assert game_service.timeout_job_name(42) != game_service.timeout_job_name(43)


def test_force_unsolved_sets_the_status(session: Session) -> None:
    game = _active_game(session)

    game_service.force_unsolved(game)
    session.commit()

    assert game.status == GameStatus.UNSOLVED


def test_seconds_until_timeout_handles_aware_datetimes() -> None:
    game = Game(starter_id=1, original_file_id="f")
    game.scheduled_end_at = datetime.now(UTC) + timedelta(seconds=100)

    assert game_service.seconds_until_timeout(game) == pytest.approx(100, abs=1)


def test_seconds_until_timeout_treats_naive_datetimes_as_utc() -> None:
    game = Game(starter_id=1, original_file_id="f")
    game.scheduled_end_at = (datetime.now(UTC) + timedelta(seconds=100)).replace(tzinfo=None)

    assert game_service.seconds_until_timeout(game) == pytest.approx(100, abs=1)


def test_seconds_until_timeout_clamps_overdue_to_zero() -> None:
    game = Game(starter_id=1, original_file_id="f")
    game.scheduled_end_at = datetime.now(UTC) - timedelta(days=1)

    assert game_service.seconds_until_timeout(game) == 0


def test_seconds_until_handles_aware_datetimes() -> None:
    deadline = datetime.now(UTC) + timedelta(seconds=100)

    assert game_service.seconds_until(deadline) == pytest.approx(100, abs=1)


def test_seconds_until_treats_naive_datetimes_as_utc() -> None:
    deadline = (datetime.now(UTC) + timedelta(seconds=100)).replace(tzinfo=None)

    assert game_service.seconds_until(deadline) == pytest.approx(100, abs=1)


def test_seconds_until_clamps_overdue_to_zero() -> None:
    deadline = datetime.now(UTC) - timedelta(days=1)

    assert game_service.seconds_until(deadline) == 0


def test_seconds_until_returns_zero_for_none() -> None:
    assert game_service.seconds_until(None) == 0


def test_set_next_starter_schedules_reminder_and_expiry_for_a_real_user(
    session: Session,
) -> None:
    session.add(Player(telegram_user_id=2))
    session.commit()
    before = datetime.now(UTC).replace(tzinfo=None)

    turn_state = game_service.set_next_starter(session, 2)
    session.commit()

    assert turn_state.next_starter_id == 2
    assert turn_state.reminder_at is not None
    assert turn_state.expiry_at is not None
    reminder_delta = (turn_state.reminder_at - before).total_seconds()
    expiry_delta = (turn_state.expiry_at - before).total_seconds()
    assert reminder_delta == pytest.approx(game_service.TURN_REMINDER_DELAY.total_seconds(), abs=5)
    assert expiry_delta == pytest.approx(game_service.TURN_EXPIRY_DELAY.total_seconds(), abs=5)


def test_set_next_starter_clears_reminder_and_expiry_when_opened(session: Session) -> None:
    session.add(Player(telegram_user_id=2))
    session.add(
        TurnState(
            id=1,
            next_starter_id=2,
            reminder_at=datetime.now(UTC),
            expiry_at=datetime.now(UTC),
        )
    )
    session.commit()

    turn_state = game_service.set_next_starter(session, None)
    session.commit()

    assert turn_state.next_starter_id is None
    assert turn_state.reminder_at is None
    assert turn_state.expiry_at is None


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


def test_clear_turn_timers_nulls_reminder_and_expiry(session: Session) -> None:
    session.add(Player(telegram_user_id=2))
    session.add(
        TurnState(
            id=1,
            next_starter_id=2,
            reminder_at=datetime.now(UTC),
            expiry_at=datetime.now(UTC),
        )
    )
    session.commit()

    game_service.clear_turn_timers(session)
    session.commit()

    turn_state = game_service.get_turn_state(session)
    assert turn_state is not None
    assert turn_state.reminder_at is None
    assert turn_state.expiry_at is None


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
    game = _active_game(session, wrong_guess_count=4, total_guess_count=4)

    game_service.record_guess(session, game, guesser_id=1, guess_text="attack on titan")
    session.commit()

    assert game.wrong_guess_count == 0
    assert game.total_guess_count == 5
