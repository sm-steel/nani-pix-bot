import pytest
from sqlalchemy.orm import Session

from nani_pix_bot.models import CurrencyTransfer, Player
from nani_pix_bot.models.enums import CurrencyReason, GameStatus, PixelStage
from nani_pix_bot.models.game import Game
from nani_pix_bot.services.economy import bounty, wallet

STARTER, ALICE, BOB = 1, 2, 3


def _setup(session: Session, *, status=GameStatus.ACTIVE) -> tuple[Game, dict[int, Player]]:
    players = {u: Player(telegram_user_id=u, currency=100) for u in (STARTER, ALICE, BOB)}
    session.add_all(players.values())
    session.flush()
    game = Game(starter_id=STARTER, status=status, current_stage=PixelStage.STAGE_1)
    session.add(game)
    session.flush()
    return game, players


def test_contributions_fill_the_pot_and_charge_contributors(session: Session) -> None:
    game, p = _setup(session)
    bounty.contribute(session, game, p[ALICE], 10)
    bounty.contribute(session, game, p[BOB], 25)
    session.flush()

    assert bounty.pot_balance(session, game.id) == 35
    assert (p[ALICE].currency, p[BOB].currency) == (90, 75)


@pytest.mark.parametrize("amount", [0, 4])
def test_contribution_below_minimum_is_refused(session: Session, amount: int) -> None:
    game, p = _setup(session)
    with pytest.raises(bounty.BountyRefusedError) as refused:
        bounty.contribute(session, game, p[ALICE], amount)
    assert refused.value.refusal is bounty.BountyRefusal.BELOW_MIN
    assert p[ALICE].currency == 100


def test_contribution_beyond_balance_is_refused(session: Session) -> None:
    game, p = _setup(session)
    with pytest.raises(bounty.BountyRefusedError) as refused:
        bounty.contribute(session, game, p[ALICE], 101)
    assert refused.value.refusal is bounty.BountyRefusal.INSUFFICIENT
    assert session.query(CurrencyTransfer).count() == 0


def test_setup_game_accepts_only_its_starter(session: Session) -> None:
    game, p = _setup(session, status=GameStatus.SETUP)
    bounty.contribute(session, game, p[STARTER], 10)
    with pytest.raises(bounty.BountyRefusedError) as refused:
        bounty.contribute(session, game, p[ALICE], 10)
    assert refused.value.refusal is bounty.BountyRefusal.NOT_OPEN


def test_finished_game_refuses_contributions(session: Session) -> None:
    game, p = _setup(session, status=GameStatus.WON)
    with pytest.raises(bounty.BountyRefusedError):
        bounty.contribute(session, game, p[ALICE], 10)


def test_payout_empties_the_pot_to_the_winner(session: Session) -> None:
    game, p = _setup(session)
    bounty.contribute(session, game, p[ALICE], 10)
    bounty.contribute(session, game, p[BOB], 20)
    session.flush()

    paid = bounty.pay_out(session, game, p[BOB])
    session.flush()

    assert paid == 30
    assert p[BOB].currency == 110
    assert bounty.pot_balance(session, game.id) == 0


def test_payout_includes_winners_own_contribution(session: Session) -> None:
    game, p = _setup(session)
    bounty.contribute(session, game, p[ALICE], 40)
    session.flush()

    assert bounty.pay_out(session, game, p[ALICE]) == 40
    session.flush()
    assert p[ALICE].currency == 100
    assert wallet.ledger_balance(session, ALICE) == 0  # -40 in, +40 out


def test_payout_of_empty_pot_moves_nothing(session: Session) -> None:
    game, p = _setup(session)
    assert bounty.pay_out(session, game, p[ALICE]) == 0
    assert session.query(CurrencyTransfer).count() == 0


def test_refund_returns_each_contribution_with_reverses_id(session: Session) -> None:
    game, p = _setup(session)
    first = bounty.contribute(session, game, p[ALICE], 10)
    bounty.contribute(session, game, p[ALICE], 15)
    bounty.contribute(session, game, p[BOB], 20)
    session.flush()

    refunded = bounty.refund_pot(session, game.id)
    session.flush()

    assert refunded == 45
    assert (p[ALICE].currency, p[BOB].currency) == (100, 100)
    assert bounty.pot_balance(session, game.id) == 0
    assert session.query(CurrencyTransfer).filter_by(reverses_id=first.id).one().amount == 10


def test_refund_after_payout_moves_nothing(session: Session) -> None:
    game, p = _setup(session)
    bounty.contribute(session, game, p[ALICE], 10)
    session.flush()
    bounty.pay_out(session, game, p[BOB])
    session.flush()

    assert bounty.refund_pot(session, game.id) == 0


def _seed_ledgered(session: Session, *, status=GameStatus.ACTIVE) -> tuple[Game, dict[int, Player]]:
    """Like _setup, but every balance comes from a wallet.credit, so the
    ledger is complete and players.currency can be audited against it."""
    players = {u: Player(telegram_user_id=u) for u in (STARTER, ALICE, BOB)}
    session.add_all(players.values())
    session.flush()
    for player in players.values():
        wallet.credit(session, player, 100, wallet.LedgerEntry(CurrencyReason.WIN))
    game = Game(starter_id=STARTER, status=status, current_stage=PixelStage.STAGE_1)
    session.add(game)
    session.flush()
    return game, players


def _assert_cache_matches_ledger(session: Session, players: dict[int, Player]) -> None:
    for user_id, player in players.items():
        assert player.currency == wallet.ledger_balance(session, user_id)


def test_cached_balances_match_the_ledger_after_contribute_and_refund(session: Session) -> None:
    game, p = _seed_ledgered(session)
    bounty.contribute(session, game, p[ALICE], 10)
    bounty.contribute(session, game, p[BOB], 25)
    session.flush()
    _assert_cache_matches_ledger(session, p)

    bounty.refund_pot(session, game.id)
    session.flush()

    assert (p[ALICE].currency, p[BOB].currency) == (100, 100)
    _assert_cache_matches_ledger(session, p)


def test_cached_balances_match_the_ledger_after_contribute_and_payout(session: Session) -> None:
    game, p = _seed_ledgered(session)
    bounty.contribute(session, game, p[ALICE], 10)
    bounty.contribute(session, game, p[BOB], 25)
    session.flush()

    bounty.pay_out(session, game, p[BOB])
    session.flush()

    assert (p[ALICE].currency, p[BOB].currency) == (90, 110)
    _assert_cache_matches_ledger(session, p)


def test_refunding_twice_refunds_once(session: Session) -> None:
    game, p = _seed_ledgered(session)
    bounty.contribute(session, game, p[ALICE], 10)
    bounty.contribute(session, game, p[BOB], 25)
    session.flush()

    first = bounty.refund_pot(session, game.id)
    second = bounty.refund_pot(session, game.id)
    session.flush()

    assert (first, second) == (35, 0)
    assert (p[ALICE].currency, p[BOB].currency) == (100, 100)
    assert (
        session.query(CurrencyTransfer).filter(CurrencyTransfer.reverses_id.is_not(None)).count()
        == 2
    )
    _assert_cache_matches_ledger(session, p)


def test_pot_balance_is_isolated_per_game(session: Session) -> None:
    first, p = _seed_ledgered(session)
    second = Game(starter_id=STARTER, status=GameStatus.ACTIVE, current_stage=PixelStage.STAGE_1)
    session.add(second)
    session.flush()
    bounty.contribute(session, first, p[ALICE], 10)
    bounty.contribute(session, second, p[BOB], 25)
    session.flush()

    assert bounty.pot_balance(session, first.id) == 10
    assert bounty.pot_balance(session, second.id) == 25

    bounty.refund_pot(session, first.id)
    session.flush()

    assert bounty.pot_balance(session, first.id) == 0
    assert bounty.pot_balance(session, second.id) == 25
