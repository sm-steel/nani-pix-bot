from sqlalchemy.orm import Session

from nani_pix_bot.commands.helpers.earnings import earnings_suffix
from nani_pix_bot.models import Player
from nani_pix_bot.models.enums import GameStatus
from nani_pix_bot.models.game import Game
from nani_pix_bot.services.economy.earning import Earnings


def _game(session: Session, *, setter_username: str | None = "setter") -> Game:
    session.add(Player(telegram_user_id=1, username=setter_username))
    session.flush()
    game = Game(starter_id=1, status=GameStatus.WON)
    session.add(game)
    session.flush()
    return game


def test_nothing_earned_gives_empty_suffix(session: Session) -> None:
    assert earnings_suffix(session, _game(session), Earnings(), "en", player_name="Ann") == ""


def test_suffix_lists_player_and_setter_lines(session: Session) -> None:
    suffix = earnings_suffix(
        session,
        _game(session),
        Earnings(guess=5, win=25, setter=15),
        "en",
        player_name="Ann",
    )

    lines = suffix.strip("\n").split("\n")
    assert len(lines) == 2
    assert "+30" in lines[0]
    assert "Ann" in lines[0]
    assert "@setter" in lines[1]
    assert "+15" in lines[1]
    assert suffix.startswith("\n")


def test_setter_without_username_uses_fallback(session: Session) -> None:
    suffix = earnings_suffix(
        session, _game(session, setter_username=None), Earnings(setter=15), "ru", player_name="A"
    )

    assert "Ведущий" in suffix


def test_bounty_line_shows_amount_and_name(session: Session) -> None:
    suffix = earnings_suffix(session, _game(session), Earnings(bounty=35), "en", player_name="Ann")

    assert suffix.startswith("\n")
    assert "+35" in suffix
    assert "Ann" in suffix


def test_no_bounty_line_when_the_pot_was_empty(session: Session) -> None:
    suffix = earnings_suffix(session, _game(session), Earnings(win=25), "en", player_name="Ann")

    assert "bounty" not in suffix
