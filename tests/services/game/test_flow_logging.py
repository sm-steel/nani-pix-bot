"""Issue #228: at LOG_LEVEL=INFO a whole game must be readable from the
log alone, so each state change leaves one INFO line. Issue #230: who and
which game travel as structured fields (the call's kwargs plus the log
context), not as text baked into the message."""

from sqlalchemy.orm import Session

from nani_pix_bot import log_context
from nani_pix_bot.models.enums import GameStatus, PixelStage, Provider
from nani_pix_bot.models.game import Game
from nani_pix_bot.models.player import Player
from nani_pix_bot.models.stage_config import StageConfig
from nani_pix_bot.services import game as game_service
from tests.conftest import LogLine


def _info(log_records: list[LogLine], message: str) -> LogLine:
    """The one INFO line with exactly this message."""
    (line,) = [r for r in log_records if r.level == "INFO" and r.message == message]
    return line


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


def test_wrong_guess_logs_text_stage_and_count_as_fields(session: Session, log_records) -> None:
    game = _active_game(session)

    game_service.record_guess(session, game, guesser_id=2, guess_text="naruto")

    line = _info(log_records, "guessed 'naruto' — wrong at stage 1/5 (1/2)")
    assert line.extra["game_id"] == game.id
    assert line.extra["guess"] == "naruto"
    assert (line.extra["wrong"], line.extra["limit"]) == (1, 2)


def test_stage_advance_logs_the_new_stage_and_reason(session: Session, log_records) -> None:
    game = _active_game(session)
    game.wrong_guess_count = 1

    game_service.record_guess(session, game, guesser_id=2, guess_text="naruto")

    line = _info(log_records, "advanced to stage 2/5 (wrong-guess limit reached)")
    assert line.extra["game_id"] == game.id


def test_correct_guess_and_win_are_logged_at_info(session: Session, log_records) -> None:
    game = _active_game(session, stage=PixelStage.STAGE_3)

    game_service.record_guess(session, game, guesser_id=2, guess_text="frieren")

    assert (
        _info(log_records, "guessed 'frieren' — CORRECT at stage 3/5").extra["game_id"] == game.id
    )
    won = _info(log_records, "won by 2 (@bob) at stage 3/5")
    assert won.extra["winner_id"] == 2


def test_unsolved_logs_its_cause(session: Session, log_records) -> None:
    game = _active_game(session, stage=PixelStage.STAGE_5)
    game.wrong_guess_count = 1

    game_service.record_guess(session, game, guesser_id=2, guess_text="naruto")

    line = _info(log_records, "ended UNSOLVED (final stage exhausted — wrong-guess limit reached)")
    assert line.extra["game_id"] == game.id


def test_turn_change_names_the_next_starter_and_reason(session: Session, log_records) -> None:
    session.add(Player(telegram_user_id=2, username="bob"))
    session.commit()

    game_service.set_next_starter(session, 2, reason="/skip")
    game_service.set_next_starter(session, None, reason="turn expired")

    line = _info(log_records, "turn designated to 2 (@bob) — /skip")
    assert line.extra["next_starter_id"] == 2
    _info(log_records, "turn opened to anyone — turn expired")


def test_game_creation_says_how_it_was_started(session: Session, log_records) -> None:
    session.add(Player(telegram_user_id=1, username="setter"))
    session.commit()

    photo = game_service.create_setup_game(session, starter_id=1, original_image=b"img")
    photo.status = GameStatus.WON
    bare = game_service.create_setup_game(session, starter_id=1)

    by_photo = _info(log_records, "created (SETUP) by 1 (@setter) via DM photo")
    assert (by_photo.extra["game_id"], by_photo.extra["starter_id"]) == (photo.id, 1)
    assert (
        _info(log_records, "created (SETUP) by 1 (@setter) via /newgame").extra["game_id"]
        == bare.id
    )


def test_activation_names_starter_and_answer(session: Session, log_records) -> None:
    session.add(Player(telegram_user_id=1, username="setter"))
    session.commit()
    game = game_service.create_setup_game(session, starter_id=1, original_image=b"img")
    game.title_romaji = "Sousou no Frieren"
    game.source = Provider.SHIKIMORI

    game_service.activate_game(session, game)

    line = _info(
        log_records,
        "ACTIVE — started by 1 (@setter), source=shikimori, answer 'Sousou no Frieren', "
        "hard_mode=False",
    )
    assert line.extra["answer"] == "Sousou no Frieren"


def test_lines_pick_up_the_update_context(session: Session, log_records) -> None:
    """The guesser isn't named in the message: the update's log context
    already carries them (commands/helpers/log_scope.py)."""
    game = _active_game(session)
    log_context.reset(user_id=2, username="bob")

    game_service.record_guess(session, game, guesser_id=2, guess_text="naruto")

    line = _info(log_records, "guessed 'naruto' — wrong at stage 1/5 (1/2)")
    assert (line.extra["user_id"], line.extra["username"]) == (2, "bob")


def test_game_lookups_bind_the_game_into_the_log_context(session: Session) -> None:
    """Issue #230: whichever handler or job finds the game, its id rides
    along on every later line of that update or job."""
    game = _active_game(session)
    log_context.reset()

    assert game_service.active_or_setup_game(session) is game
    assert log_context.current() == {"game_id": game.id}


def test_a_lookup_that_finds_nothing_binds_nothing(session: Session) -> None:
    _active_game(session)
    log_context.reset()

    assert game_service.get_setup_game_for_starter(session, 1) is None
    assert log_context.current() == {}


def test_creating_a_game_binds_it(session: Session) -> None:
    log_context.reset()
    game = game_service.create_setup_game(session, starter_id=1, original_image=b"img")

    assert log_context.current() == {"game_id": game.id}
