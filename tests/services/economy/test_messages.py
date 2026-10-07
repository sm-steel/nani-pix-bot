from datetime import UTC, datetime

from sqlalchemy.orm import Session

from nani_pix_bot.models import Player
from nani_pix_bot.models.enums import EventType, GameStatus
from nani_pix_bot.models.game import Game
from nani_pix_bot.services import events
from nani_pix_bot.services.economy.earning import Earnings
from nani_pix_bot.services.economy.messages import earnings_suffix


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


def test_suffix_has_a_compensation_line(session: Session) -> None:
    suffix = earnings_suffix(
        session, _game(session), Earnings(win=25, compensation=30), "en", player_name="Ann"
    )

    assert "+30" in suffix.splitlines()[-1]
    assert "Ann" in suffix.splitlines()[-1]


def test_a_won_game_gets_the_champion_score_line_after_the_earnings(session: Session) -> None:
    game = _game(session)
    session.add(Player(telegram_user_id=2, username="ann"))
    session.flush()
    events.emit(
        session,
        EventType.GAME_WON,
        events.Involved(actor_id=2, subject_id=1, game_id=game.id),
        stage=1,
        hard_mode=False,
        how="guess",
        pot=0,
        seconds=None,
        last_slot=False,
        distinct_guessers=1,
        winner_wrong=0,
        first_guess=False,
        ended_at=datetime.now(UTC).isoformat(),
    )

    lines = (
        earnings_suffix(session, game, Earnings(win=25), "en", player_name="Ann")
        .strip("\n")
        .split("\n")
    )

    assert "+25" in lines[0]
    assert lines[1].startswith("+5 🌟 → week 5 (#1 🆕)")
    assert lines[2] == "Host @setter +1 🌟"
