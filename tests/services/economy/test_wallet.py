import pytest
from sqlalchemy.orm import Session

from nani_pix_bot.models import PixelTransaction, Player
from nani_pix_bot.models.enums import GameStatus, PixelReason
from nani_pix_bot.models.game import Game
from nani_pix_bot.services.economy import wallet


def _player(session: Session, user_id: int = 1, pixels: int = 0) -> Player:
    player = Player(telegram_user_id=user_id, pixels=pixels)
    session.add(player)
    session.flush()
    return player


def _game(session: Session, starter_id: int = 1) -> Game:
    game = Game(starter_id=starter_id, status=GameStatus.ACTIVE)
    session.add(game)
    session.flush()
    return game


def test_credit_moves_balance_and_writes_ledger_row(session: Session) -> None:
    player = _player(session)
    game = _game(session)

    tx = wallet.credit(
        session,
        player,
        7,
        wallet.LedgerEntry(PixelReason.WIN, game_id=game.id),
    )
    session.commit()

    assert player.pixels == 7
    assert tx.amount == 7
    assert tx.reason == PixelReason.WIN
    assert tx.game_id == game.id


def test_credit_rejects_non_positive(session: Session) -> None:
    player = _player(session)
    with pytest.raises(ValueError, match="positive"):
        wallet.credit(session, player, 0, wallet.LedgerEntry(PixelReason.WIN))


def test_debit_moves_balance_negative_in_ledger(session: Session) -> None:
    player = _player(session, pixels=30)

    tx = wallet.debit(session, player, 20, wallet.LedgerEntry(PixelReason.GRANT))

    assert player.pixels == 10
    assert tx.amount == -20


def test_debit_refuses_overdraft_and_changes_nothing(session: Session) -> None:
    player = _player(session, pixels=5)

    with pytest.raises(wallet.InsufficientPixelsError):
        wallet.debit(session, player, 6, wallet.LedgerEntry(PixelReason.GRANT))

    assert player.pixels == 5
    assert session.query(PixelTransaction).count() == 0


def test_balance_is_zero_for_unknown_player(session: Session) -> None:
    assert wallet.balance(session, 404) == 0


def test_game_total_sums_one_reason_for_one_player_in_one_game(session: Session) -> None:
    alice = _player(session, 1)
    bob = _player(session, 2)
    game = _game(session)
    other = _game(session)
    wallet.credit(
        session,
        alice,
        2,
        wallet.LedgerEntry(PixelReason.WRONG_GUESS, game_id=game.id),
    )
    wallet.credit(
        session,
        alice,
        2,
        wallet.LedgerEntry(PixelReason.WRONG_GUESS, game_id=game.id),
    )
    wallet.credit(
        session,
        alice,
        5,
        wallet.LedgerEntry(PixelReason.FIRST_GUESS, game_id=game.id),
    )
    wallet.credit(
        session,
        alice,
        2,
        wallet.LedgerEntry(PixelReason.WRONG_GUESS, game_id=other.id),
    )
    wallet.credit(
        session,
        bob,
        2,
        wallet.LedgerEntry(PixelReason.WRONG_GUESS, game_id=game.id),
    )
    session.flush()

    total = wallet.game_total(session, player_id=1, game_id=game.id, reason=PixelReason.WRONG_GUESS)
    assert total == 4
