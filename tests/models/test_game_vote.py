import pytest
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from nani_pix_bot.models import GameVote, Player
from nani_pix_bot.models.enums import GameStatus
from nani_pix_bot.models.game import Game


def test_one_vote_row_per_game_and_voter(session: Session) -> None:
    session.add_all([Player(telegram_user_id=n) for n in (1, 2, 3)])
    session.flush()
    game = Game(starter_id=1, status=GameStatus.VOTING)
    session.add(game)
    session.flush()
    session.add(GameVote(game_id=game.id, voter_id=2, candidate_id=3))
    session.flush()
    session.add(GameVote(game_id=game.id, voter_id=2, candidate_id=1))
    with pytest.raises(IntegrityError):
        session.flush()
