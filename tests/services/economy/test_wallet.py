import pytest
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from nani_pix_bot.models import PixelTransfer, Player
from nani_pix_bot.models.enums import GameStatus, PixelParty, PixelReason
from nani_pix_bot.models.game import Game
from nani_pix_bot.services.economy import wallet
from nani_pix_bot.services.economy.wallet import Party


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


def test_credit_moves_balance_and_writes_house_to_player_row(session: Session) -> None:
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
    assert tx.from_type == PixelParty.HOUSE
    assert tx.from_player_id is None
    assert tx.to_type == PixelParty.PLAYER
    assert tx.to_player_id == player.telegram_user_id
    assert tx.reason == PixelReason.WIN
    assert tx.game_id == game.id


def test_credit_rejects_non_positive(session: Session) -> None:
    player = _player(session)
    with pytest.raises(ValueError, match="positive"):
        wallet.credit(session, player, 0, wallet.LedgerEntry(PixelReason.WIN))


def test_debit_writes_player_to_house_row(session: Session) -> None:
    player = _player(session, pixels=30)

    tx = wallet.debit(session, player, 20, wallet.LedgerEntry(PixelReason.GRANT))

    assert player.pixels == 10
    assert tx.amount == 20
    assert tx.from_type == PixelParty.PLAYER
    assert tx.from_player_id == player.telegram_user_id
    assert tx.to_type == PixelParty.HOUSE
    assert tx.to_player_id is None


def test_debit_refuses_overdraft_and_changes_nothing(session: Session) -> None:
    player = _player(session, pixels=5)

    with pytest.raises(wallet.InsufficientPixelsError):
        wallet.debit(session, player, 6, wallet.LedgerEntry(PixelReason.GRANT))

    assert player.pixels == 5
    assert session.query(PixelTransfer).count() == 0


def test_transfer_player_to_player_moves_both_balances_in_one_row(session: Session) -> None:
    alice = _player(session, 1, pixels=10)
    bob = _player(session, 2, pixels=1)

    row = wallet.transfer(
        session, Party.of(alice), Party.of(bob), 4, wallet.LedgerEntry(PixelReason.GRANT)
    )
    session.flush()

    assert (alice.pixels, bob.pixels) == (6, 5)
    assert session.query(PixelTransfer).count() == 1
    assert (row.from_player_id, row.to_player_id) == (1, 2)


def test_transfer_rejects_non_positive_amount(session: Session) -> None:
    alice = _player(session, 1, pixels=10)
    with pytest.raises(ValueError, match="positive"):
        wallet.transfer(
            session, Party.of(alice), Party.house(), 0, wallet.LedgerEntry(PixelReason.GRANT)
        )


@pytest.mark.parametrize("kind", ["player", "house", "pot"])
def test_transfer_rejects_same_party_before_touching_anything(session: Session, kind: str) -> None:
    alice = _player(session, 1, pixels=10)
    parties = {"player": Party.of(alice), "house": Party.house(), "pot": Party.pot()}
    same = parties[kind]

    with pytest.raises(ValueError, match="same"):
        wallet.transfer(session, same, same, 3, wallet.LedgerEntry(PixelReason.GRANT, game_id=1))

    assert alice.pixels == 10
    assert session.query(PixelTransfer).count() == 0


def test_transfer_player_to_pot_requires_game_id(session: Session) -> None:
    alice = _player(session, 1, pixels=10)
    wallet.transfer(session, Party.of(alice), Party.pot(), 3, wallet.LedgerEntry(PixelReason.GRANT))

    with pytest.raises(IntegrityError):
        session.flush()


def test_transfer_player_to_pot_with_game_succeeds(session: Session) -> None:
    alice = _player(session, 1, pixels=10)
    game = _game(session)

    row = wallet.transfer(
        session,
        Party.of(alice),
        Party.pot(),
        3,
        wallet.LedgerEntry(PixelReason.GRANT, game_id=game.id),
    )
    session.flush()

    assert alice.pixels == 7
    assert row.to_type == PixelParty.POT
    assert row.to_player_id is None


def test_party_requires_player_exactly_when_type_is_player() -> None:
    player = Player(telegram_user_id=1)
    with pytest.raises(ValueError, match="player"):
        Party(PixelParty.PLAYER)
    with pytest.raises(ValueError, match="player"):
        Party(PixelParty.HOUSE, player)
    with pytest.raises(ValueError, match="player"):
        Party(PixelParty.POT, player)
    assert Party.of(player).type is PixelParty.PLAYER
    assert Party.house().player is None
    assert Party.pot().player is None


def test_balance_is_zero_for_unknown_player(session: Session) -> None:
    assert wallet.balance(session, 404) == 0


def test_game_total_sums_one_reason_for_one_player_in_one_game(session: Session) -> None:
    alice = _player(session, 1)
    bob = _player(session, 2)
    game = _game(session)
    other = _game(session)
    for player, reason, game_id, amount in [
        (alice, PixelReason.WRONG_GUESS, game.id, 2),
        (alice, PixelReason.WRONG_GUESS, game.id, 2),
        (alice, PixelReason.FIRST_GUESS, game.id, 5),
        (alice, PixelReason.WRONG_GUESS, other.id, 2),
        (bob, PixelReason.WRONG_GUESS, game.id, 2),
    ]:
        wallet.credit(session, player, amount, wallet.LedgerEntry(reason, game_id=game_id))
    session.flush()

    total = wallet.game_total(session, player_id=1, game_id=game.id, reason=PixelReason.WRONG_GUESS)
    assert total == 4


def test_game_total_counts_only_what_a_player_received(session: Session) -> None:
    alice = _player(session, 1, pixels=20)
    game = _game(session)
    entry = wallet.LedgerEntry(PixelReason.WRONG_GUESS, game_id=game.id)
    wallet.credit(session, alice, 2, entry)
    wallet.debit(session, alice, 5, entry)
    wallet.transfer(session, Party.of(alice), Party.pot(), 4, entry)
    session.flush()

    total = wallet.game_total(session, player_id=1, game_id=game.id, reason=PixelReason.WRONG_GUESS)
    assert total == 2


def test_cached_balance_always_equals_ledger_balance(session: Session) -> None:
    alice = _player(session, 1)
    bob = _player(session, 2)
    game = _game(session)
    entry = wallet.LedgerEntry(PixelReason.GRANT, game_id=game.id)

    wallet.credit(session, alice, 50, entry)
    wallet.credit(session, bob, 20, entry)
    wallet.debit(session, alice, 5, entry)
    with pytest.raises(wallet.InsufficientPixelsError):
        wallet.transfer(session, Party.of(bob), Party.of(alice), 999, entry)
    wallet.transfer(session, Party.of(alice), Party.of(bob), 12, entry)
    with pytest.raises(wallet.InsufficientPixelsError):
        wallet.debit(session, alice, 999, entry)
    wallet.transfer(session, Party.of(alice), Party.pot(), 10, entry)
    wallet.transfer(session, Party.pot(), Party.of(bob), 4, entry)
    session.flush()

    for player in (alice, bob):
        assert player.pixels == wallet.ledger_balance(session, player.telegram_user_id)
    assert (alice.pixels, bob.pixels) == (23, 36)
    assert wallet.ledger_balance(session, 404) == 0
