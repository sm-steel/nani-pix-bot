from sqlalchemy.orm import Session

from nani_pix_bot.models.player import Player
from nani_pix_bot.services import players


def test_top_players_orders_by_wins_descending(session: Session) -> None:
    session.add_all(
        [
            Player(telegram_user_id=1, username="low", wins=1),
            Player(telegram_user_id=2, username="high", wins=5),
            Player(telegram_user_id=3, username="mid", wins=3),
        ]
    )
    session.commit()

    top = players.top_players(session, limit=10)

    assert [p.username for p in top] == ["high", "mid", "low"]


def test_top_players_respects_the_limit(session: Session) -> None:
    session.add_all(
        [
            Player(telegram_user_id=1, wins=1),
            Player(telegram_user_id=2, wins=2),
            Player(telegram_user_id=3, wins=3),
        ]
    )
    session.commit()

    top = players.top_players(session, limit=2)

    assert len(top) == 2
    assert top[0].wins == 3
    assert top[1].wins == 2


def test_top_players_excludes_players_with_zero_wins(session: Session) -> None:
    session.add_all(
        [
            Player(telegram_user_id=1, wins=0),
            Player(telegram_user_id=2, wins=1),
        ]
    )
    session.commit()

    top = players.top_players(session, limit=10)

    assert [p.telegram_user_id for p in top] == [2]


def test_top_players_returns_empty_list_when_no_one_has_won(session: Session) -> None:
    session.add(Player(telegram_user_id=1, wins=0))
    session.commit()

    assert players.top_players(session, limit=10) == []
