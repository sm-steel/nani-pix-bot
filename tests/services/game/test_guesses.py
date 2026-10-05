from sqlalchemy.orm import Session

from nani_pix_bot.models import GameGuess, Player
from nani_pix_bot.models.enums import GameStatus
from nani_pix_bot.models.game import Game
from nani_pix_bot.models.game_guess import GUESS_TEXT_LENGTH
from nani_pix_bot.services.game import guesses


def _game(session: Session) -> Game:
    session.add_all([Player(telegram_user_id=n) for n in (1, 2, 3)])
    session.flush()
    game = Game(starter_id=1, status=GameStatus.ACTIVE)
    session.add(game)
    session.flush()
    return game


def test_log_guess_stores_its_fields(session: Session) -> None:
    game = _game(session)
    guesses.log_guess(
        session, game, guesses.GuessRecord(player_id=2, text="naruto", stage=3, correct=True)
    )

    row = session.query(GameGuess).one()
    assert (row.game_id, row.player_id, row.text, row.stage, row.correct) == (
        game.id,
        2,
        "naruto",
        3,
        True,
    )
    assert row.partial_reveal is None


def test_log_guess_stores_partial_reveal(session: Session) -> None:
    game = _game(session)
    row = guesses.log_guess(
        session,
        game,
        guesses.GuessRecord(player_id=2, text="b", stage=1, correct=False, partial_reveal="_ _"),
    )
    assert row.partial_reveal == "_ _"


def test_long_guess_is_truncated(session: Session) -> None:
    game = _game(session)
    row = guesses.log_guess(
        session, game, guesses.GuessRecord(player_id=2, text="x" * 1000, stage=1, correct=False)
    )
    assert len(row.text) == GUESS_TEXT_LENGTH


def test_latest_is_the_newest_row_or_none(session: Session) -> None:
    game = _game(session)
    assert guesses.latest(session, game.id) is None

    for text in ("a", "b"):
        guesses.log_guess(
            session, game, guesses.GuessRecord(player_id=2, text=text, stage=1, correct=False)
        )

    latest = guesses.latest(session, game.id)
    assert latest is not None
    assert latest.text == "b"


def test_has_partial_reveal_only_when_a_row_carries_one(session: Session) -> None:
    game = _game(session)
    assert guesses.has_partial_reveal(session, game.id) is False

    guesses.log_guess(
        session, game, guesses.GuessRecord(player_id=2, text="a", stage=1, correct=False)
    )
    assert guesses.has_partial_reveal(session, game.id) is False

    guesses.log_guess(
        session,
        game,
        guesses.GuessRecord(player_id=2, text="b", stage=1, correct=False, partial_reveal="_ _"),
    )
    assert guesses.has_partial_reveal(session, game.id) is True
