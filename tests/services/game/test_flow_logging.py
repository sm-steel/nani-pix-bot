"""Issue #228: at LOG_LEVEL=INFO a whole game must be readable from the
log alone. Each state change leaves one INFO line that starts with
`Game {id}:` and names who acted, so `grep "Game 88"` recovers the game."""

from sqlalchemy.orm import Session

from nani_pix_bot.models.enums import GameStatus, PixelStage, Provider
from nani_pix_bot.models.game import Game
from nani_pix_bot.models.player import Player
from nani_pix_bot.models.stage_config import StageConfig
from nani_pix_bot.services import game as game_service


def _info(records: list[tuple[str, str]]) -> list[str]:
    return [message for level, message in records if level == "INFO"]


def _active_game(session: Session, *, stage: PixelStage = PixelStage.STAGE_1) -> Game:
    session.add_all(
        [
            Player(telegram_user_id=1, username="setter"),
            Player(telegram_user_id=2, username="bob"),
        ]
    )
    session.add(StageConfig(stage=stage, target_width=100, wrong_guess_limit=2))
    game = Game(
        starter_id=1,
        original_image=b"img",
        status=GameStatus.ACTIVE,
        current_stage=stage,
        title_romaji="Sousou no Frieren",
        synonyms=["Frieren"],
    )
    session.add(game)
    session.commit()
    return game


def test_stage_label_is_human_readable() -> None:
    assert game_service.stage_label(PixelStage.STAGE_2) == "stage 2/5"


def test_wrong_guess_logs_guesser_text_and_count_at_info(session: Session, records) -> None:
    game = _active_game(session)

    game_service.record_guess(session, game, guesser_id=2, guess_text="naruto")

    assert f"Game {game.id}: 2 (@bob) guessed 'naruto' — wrong at stage 1/5 (1/2)" in _info(records)


def test_stage_advance_logs_the_new_stage_and_reason(session: Session, records) -> None:
    game = _active_game(session)
    game.wrong_guess_count = 1

    game_service.record_guess(session, game, guesser_id=2, guess_text="naruto")

    assert f"Game {game.id}: advanced to stage 2/5 (wrong-guess limit reached)" in _info(records)


def test_correct_guess_and_win_are_logged_at_info(session: Session, records) -> None:
    game = _active_game(session, stage=PixelStage.STAGE_3)

    game_service.record_guess(session, game, guesser_id=2, guess_text="frieren")

    info = _info(records)
    assert f"Game {game.id}: 2 (@bob) guessed 'frieren' — CORRECT at stage 3/5" in info
    assert f"Game {game.id}: won by 2 (@bob) at stage 3/5" in info


def test_unsolved_logs_its_cause(session: Session, records) -> None:
    game = _active_game(session, stage=PixelStage.STAGE_5)
    game.wrong_guess_count = 1

    game_service.record_guess(session, game, guesser_id=2, guess_text="naruto")

    assert (
        f"Game {game.id}: ended UNSOLVED (final stage exhausted — wrong-guess limit reached)"
        in _info(records)
    )


def test_turn_change_names_the_reason(session: Session, records) -> None:
    session.add(Player(telegram_user_id=2, username="bob"))
    session.commit()

    game_service.set_next_starter(session, 2, reason="/skip by 1 (@setter)")
    game_service.set_next_starter(session, None, reason="turn expired")

    info = _info(records)
    assert "Turn designated to 2 (@bob) — /skip by 1 (@setter)" in info
    assert "Turn opened to anyone — turn expired" in info


def test_game_creation_says_how_it_was_started(session: Session, records) -> None:
    session.add(Player(telegram_user_id=1, username="setter"))
    session.commit()

    photo = game_service.create_setup_game(session, starter_id=1, original_image=b"img")
    photo.status = GameStatus.WON
    bare = game_service.create_setup_game(session, starter_id=1)

    info = _info(records)
    assert f"Game {photo.id}: created (SETUP) by 1 (@setter) via DM photo" in info
    assert f"Game {bare.id}: created (SETUP) by 1 (@setter) via /newgame" in info


def test_activation_names_starter_and_answer(session: Session, records) -> None:
    session.add(Player(telegram_user_id=1, username="setter"))
    session.commit()
    game = game_service.create_setup_game(session, starter_id=1, original_image=b"img")
    game.title_romaji = "Sousou no Frieren"
    game.source = Provider.SHIKIMORI

    game_service.activate_game(session, game)

    assert (
        f"Game {game.id}: ACTIVE — started by 1 (@setter), source=shikimori, "
        "answer 'Sousou no Frieren'"
    ) in _info(records)
