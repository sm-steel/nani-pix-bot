from sqlalchemy.orm import Session

from nani_pix_bot.models.enums import CurrencyReason, GameStatus
from nani_pix_bot.models.game import Game
from nani_pix_bot.models.player import Player
from nani_pix_bot.services import i18n
from nani_pix_bot.services.economy import bounty, config, settlement, wallet
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


def test_settle_disputed_win_pays_the_hard_mode_win_plus_compensation(session: Session) -> None:
    game = _game(session, turn=2)
    winner = session.get(Player, 2)
    assert winner is not None
    start = winner.currency

    earnings = settlement.settle_disputed_win(session, game, 2)

    win = config.get_amounts(session)[EconomyKey.WIN_STAGE_2] * 2
    assert earnings.win == win
    assert earnings.compensation == 30
    assert winner.currency == start + win + 30


def test_settle_unsolved_pays_cashback_and_says_so(session: Session) -> None:
    game = _game(session)
    buyer = session.get(Player, 2)
    assert buyer is not None
    wallet.debit(
        session, buyer, 80, wallet.LedgerEntry(CurrencyReason.CLUE_PURCHASE, game_id=game.id)
    )

    note = settlement.settle_unsolved(session, game, "en")

    assert i18n.t("economy.cashback", "en", amount=40) in note
    assert buyer.currency == 100 - 80 + 40


def test_refinish_after_cashback_keeps_the_cashback(session: Session) -> None:
    game = _game(session, turn=2)
    buyer = session.get(Player, 2)
    assert buyer is not None
    wallet.debit(
        session, buyer, 80, wallet.LedgerEntry(CurrencyReason.CLUE_PURCHASE, game_id=game.id)
    )
    settlement.settle_unsolved(session, game, "en")

    earnings = settlement.settle_disputed_win(session, game, 2)

    win = config.get_amounts(session)[EconomyKey.WIN_STAGE_2] * 2
    assert buyer.currency == 100 - 80 + 40 + win + earnings.compensation
