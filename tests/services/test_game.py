import pytest
from sqlalchemy.orm import Session

from nani_pix_bot.models.enums import GameStatus, PixelStage
from nani_pix_bot.models.game import Game
from nani_pix_bot.models.player import Player
from nani_pix_bot.models.turn_state import TurnState
from nani_pix_bot.services import game as game_service
from nani_pix_bot.services.anilist import AniListResult

_FRIEREN = AniListResult(
    anilist_id=99,
    title_romaji="Sousou no Frieren",
    title_english="Frieren: Beyond Journey's End",
    title_native="葬送のフリーレン",
    synonyms=["Frieren"],
    year=2023,
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


def test_activate_game_sets_active_state_and_opens_the_turn(session: Session) -> None:
    session.add(Player(telegram_user_id=1))
    session.add(TurnState(id=1, next_starter_id=1))
    session.commit()
    game = game_service.create_setup_game(session, starter_id=1, original_file_id="file123")
    session.commit()

    game_service.activate_game(session, game, _FRIEREN)
    session.commit()

    fetched = session.get(Game, game.id)
    assert fetched is not None
    assert fetched.status == GameStatus.ACTIVE
    assert fetched.current_stage == PixelStage.X10
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

    game_service.activate_game(session, game, _FRIEREN)
    session.commit()

    turn_state = session.get(TurnState, 1)
    assert turn_state is not None
    assert turn_state.next_starter_id is None


def _active_game(
    session: Session, *, stage: PixelStage = PixelStage.X10, wrong_guess_count: int = 0
) -> Game:
    session.add(Player(telegram_user_id=1))
    session.commit()
    game = Game(
        starter_id=1,
        original_file_id="file123",
        status=GameStatus.ACTIVE,
        current_stage=stage,
        wrong_guess_count=wrong_guess_count,
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


def test_record_guess_wrong_increments_the_counter_without_advancing(session: Session) -> None:
    game = _active_game(session, wrong_guess_count=3)

    outcome = game_service.record_guess(session, game, guesser_id=1, guess_text="attack on titan")
    session.commit()

    assert outcome is game_service.GuessOutcome.WRONG
    assert game.status == GameStatus.ACTIVE
    assert game.current_stage == PixelStage.X10
    assert game.wrong_guess_count == 4


def test_record_guess_advances_stage_on_the_fifth_wrong_guess(session: Session) -> None:
    game = _active_game(session, stage=PixelStage.X10, wrong_guess_count=4)

    outcome = game_service.record_guess(session, game, guesser_id=1, guess_text="attack on titan")
    session.commit()

    assert outcome is game_service.GuessOutcome.STAGE_ADVANCED
    assert game.status == GameStatus.ACTIVE
    assert game.current_stage == PixelStage.X8
    assert game.wrong_guess_count == 0


def test_record_guess_ends_unsolved_after_x2_stage_exhaustion(session: Session) -> None:
    game = _active_game(session, stage=PixelStage.X2, wrong_guess_count=4)

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
