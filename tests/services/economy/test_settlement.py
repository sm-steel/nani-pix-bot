from sqlalchemy.orm import Session

from nani_pix_bot.models.enums import GameStatus
from nani_pix_bot.models.game import Game
from nani_pix_bot.models.player import Player
from nani_pix_bot.services import i18n
from nani_pix_bot.services.economy import bounty, config, settlement
from nani_pix_bot.services.economy.config import EconomyKey


def _game(session: Session, *, turn: int = 2) -> Game:
    session.add_all([Player(telegram_user_id=1), Player(telegram_user_id=2, currency=100)])
    session.flush()
    game = Game(starter_id=1, status=GameStatus.ACTIVE, hard_mode=True, hard_mode_turn=turn)
    session.add(game)
    session.flush()
    return game


def test_settle_unsolved_refunds_a_funded_pot_and_returns_its_line(session: Session) -> None:
    game = _game(session)
    funder = session.get(Player, 2)
    assert funder is not None
    bounty.contribute(session, game, funder, 30)
    assert funder.currency == 70

    note = settlement.settle_unsolved(session, game, "en")

    assert note == "\n" + i18n.t("economy.bounty_refunded", "en", amount=30)
    assert funder.currency == 100
    assert bounty.pot_balance(session, game.id) == 0


def test_settle_unsolved_with_an_empty_pot_adds_nothing(session: Session) -> None:
    assert settlement.settle_unsolved(session, _game(session), "en") == ""


def test_settle_vote_win_pays_the_hard_mode_win_reward(session: Session) -> None:
    game = _game(session, turn=2)
    winner = session.get(Player, 2)
    assert winner is not None
    start = winner.currency

    earnings = settlement.settle_vote_win(session, game, 2)

    expected = config.get_amounts(session)[EconomyKey.WIN_STAGE_2] * 2
    assert earnings.win == expected
    assert winner.currency == start + expected
