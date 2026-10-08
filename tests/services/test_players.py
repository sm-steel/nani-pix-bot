from zoneinfo import ZoneInfo

from sqlalchemy.orm import Session

from nani_pix_bot.models.achievement import AchievementGrant
from nani_pix_bot.models.currency_transfer import CurrencyTransfer
from nani_pix_bot.models.enums import Rarity
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


def _grant(session: Session, player_id: int, key: str, points: int) -> None:
    session.add(
        AchievementGrant(
            player_id=player_id, key=key, tier=1, rarity=Rarity.SILVER, reward=0, points=points
        )
    )


def test_leaderboard_orders_by_wins_then_achievement_points(session: Session) -> None:
    session.add_all(
        [
            Player(telegram_user_id=1, username="low", wins=1),
            Player(telegram_user_id=2, username="high", wins=5, currency=40),
            Player(telegram_user_id=3, username="tied_less", wins=3),
            Player(telegram_user_id=4, username="tied_more", wins=3),
        ]
    )
    session.flush()
    _grant(session, 4, "clutch", 3)
    _grant(session, 4, "first_try", 2)
    _grant(session, 3, "clutch", 1)
    session.commit()

    rows = players.leaderboard(session, limit=10)

    assert [r.player_id for r in rows] == [2, 4, 3, 1]
    assert (rows[0].wins, rows[0].currency, rows[0].points) == (5, 40, 0)
    assert rows[1].points == 5


def test_leaderboard_pages_and_leaves_out_players_without_a_win(session: Session) -> None:
    session.add_all(Player(telegram_user_id=i, wins=i - 1) for i in range(1, 6))  # 1 has 0
    session.commit()

    assert [r.player_id for r in players.leaderboard(session, limit=2)] == [5, 4]
    assert [r.player_id for r in players.leaderboard(session, limit=2, offset=2)] == [3, 2]
    assert players.leaderboard_count(session) == 4
    assert players.leaderboard_rank(session, 3) == 3
    assert players.leaderboard_rank(session, 1) is None


def test_leaderboard_is_empty_when_no_one_has_won(session: Session) -> None:
    session.add(Player(telegram_user_id=1, wins=0))
    session.commit()

    assert players.leaderboard(session, limit=10) == []
    assert players.leaderboard_count(session) == 0


def test_timezone_defaults_to_none(session: Session) -> None:
    assert players.get_timezone(session, 42) is None


def test_set_timezone_creates_player_and_round_trips(session: Session) -> None:
    players.set_timezone(session, 42, ZoneInfo("Asia/Novosibirsk"))
    session.commit()
    assert players.get_timezone(session, 42) == ZoneInfo("Asia/Novosibirsk")


def test_get_or_create_player_grants_starting_balance_once(session: Session) -> None:
    players.get_or_create_player(session, 1, username="frieren")
    players.get_or_create_player(session, 1, username="frieren")
    session.commit()

    player = session.get(Player, 1)
    assert player is not None
    assert player.currency == 50


def test_get_or_create_player_without_grant_starts_at_zero_with_no_ledger_row(
    session: Session,
) -> None:
    players.get_or_create_player(session, 1, username="bot", grant=False)
    session.commit()

    player = session.get(Player, 1)
    assert player is not None
    assert player.currency == 0
    assert session.query(CurrencyTransfer).count() == 0


def test_describe_person_prefers_the_username() -> None:
    assert players.describe_person(5, username="bob", name="Bob B") == "5 (@bob)"


def test_describe_person_falls_back_to_the_name_then_the_bare_id() -> None:
    assert players.describe_person(5, name="Bob B") == "5 (Bob B)"
    assert players.describe_person(5) == "5"


def test_describe_player_id_reads_the_stored_username(session: Session) -> None:
    session.add(Player(telegram_user_id=7, username="carol"))
    session.commit()

    assert players.describe_player_id(session, 7) == "7 (@carol)"
    assert players.describe_player_id(session, 8) == "8"


def test_display_name_prefers_the_handle_and_falls_back_to_the_id(session: Session) -> None:
    session.add_all([Player(telegram_user_id=1, username="bob"), Player(telegram_user_id=2)])
    session.flush()

    assert players.display_name(session, 1) == "@bob"
    assert players.display_name(session, 2) == "2"
    assert players.display_name(session, 3) == "3"


def test_display_name_falls_back_to_the_first_name(session: Session) -> None:
    player = Player(telegram_user_id=4)
    session.add(player)
    players.remember_first_name(player, "Карина")

    assert players.display_name(session, 4) == "Карина"


def test_remember_first_name_never_blanks_a_known_name() -> None:
    player = Player(telegram_user_id=4, first_name="Kari")
    players.remember_first_name(player, None)
    assert player.first_name == "Kari"
