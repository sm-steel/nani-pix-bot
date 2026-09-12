from sqlalchemy.orm import Session

from nani_pix_bot.models.player import Player
from nani_pix_bot.services import players


def test_get_or_create_player_creates_a_new_row(session: Session) -> None:
    player = players.get_or_create_player(session, 1, username="frieren")
    session.commit()

    fetched = session.get(Player, 1)
    assert fetched is not None
    assert fetched.username == "frieren"
    assert player.telegram_user_id == 1


def test_get_or_create_player_refreshes_username_on_an_existing_row(session: Session) -> None:
    session.add(Player(telegram_user_id=1, username="old", wins=3))
    session.commit()

    player = players.get_or_create_player(session, 1, username="new")
    session.commit()

    assert player.wins == 3
    assert player.username == "new"


def test_find_player_by_username_finds_a_case_insensitive_match(session: Session) -> None:
    session.add(Player(telegram_user_id=1, username="Frieren"))
    session.commit()

    found = players.find_player_by_username(session, "frieren")

    assert found is not None
    assert found.telegram_user_id == 1


def test_find_player_by_username_returns_none_when_unknown(session: Session) -> None:
    assert players.find_player_by_username(session, "nobody") is None


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
