import pytest
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from nani_pix_bot.models.enums import PeriodType
from nani_pix_bot.models.period import PeriodResult
from nani_pix_bot.models.player import Player


def _result(player_id: int, rank: int) -> PeriodResult:
    return PeriodResult(
        period_type=PeriodType.WEEK,
        period_key="2026-W41",
        rank=rank,
        player_id=player_id,
        score=6,
        wins=1,
    )


def test_tied_players_can_share_a_rank(session: Session) -> None:
    session.add_all([Player(telegram_user_id=1), Player(telegram_user_id=2)])
    session.flush()
    session.add_all([_result(1, 1), _result(2, 1)])
    session.flush()


def test_a_player_has_one_place_per_period(session: Session) -> None:
    session.add(Player(telegram_user_id=1))
    session.flush()
    session.add_all([_result(1, 1), _result(1, 2)])
    with pytest.raises(IntegrityError):
        session.flush()
