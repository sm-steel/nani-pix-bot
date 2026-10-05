from sqlalchemy.orm import Session

from nani_pix_bot.models import GameGuess, Player
from nani_pix_bot.models.enums import GameStatus
from nani_pix_bot.models.game import Game


def test_guess_row_defaults(session: Session) -> None:
    session.add_all([Player(telegram_user_id=1), Player(telegram_user_id=2)])
    session.flush()
    game = Game(starter_id=1, status=GameStatus.ACTIVE)
    session.add(game)
    session.flush()
    session.add(GameGuess(game_id=game.id, player_id=2, text="naruto", stage=1, correct=False))
    session.commit()

    row = session.query(GameGuess).one()
    assert row.partial_reveal is None
    assert row.created_at is not None
