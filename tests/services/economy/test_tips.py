import pytest
from sqlalchemy.orm import Session

from nani_pix_bot.models import CurrencyTransfer, Player
from nani_pix_bot.models.enums import CurrencyParty, CurrencyReason
from nani_pix_bot.services.economy import tips, wallet
from tests.services.economy.ledger import ledger_balance

ALICE, BOB = 1, 2


def _seed(session: Session) -> tuple[Player, Player]:
    alice = Player(telegram_user_id=ALICE)
    bob = Player(telegram_user_id=BOB)
    session.add_all([alice, bob])
    session.flush()
    wallet.credit(session, alice, 50, wallet.LedgerEntry(CurrencyReason.WIN))
    wallet.credit(session, bob, 10, wallet.LedgerEntry(CurrencyReason.WIN))
    session.flush()
    return alice, bob


def _tip_rows(session: Session) -> list[CurrencyTransfer]:
    return list(
        session.query(CurrencyTransfer).where(CurrencyTransfer.reason == CurrencyReason.TIP)
    )


def test_tip_moves_currency_as_one_player_to_player_row(session: Session) -> None:
    alice, bob = _seed(session)

    row = tips.tip(session, alice, bob, 20)
    session.flush()

    assert (alice.currency, bob.currency) == (30, 30)
    assert (row.from_type, row.to_type) == (CurrencyParty.PLAYER, CurrencyParty.PLAYER)
    assert (row.from_player_id, row.to_player_id) == (ALICE, BOB)
    assert row.amount == 20
    assert row.game_id is None
    assert row.reason == CurrencyReason.TIP
    assert len(_tip_rows(session)) == 1
    for player in (alice, bob):
        assert player.currency == ledger_balance(session, player.telegram_user_id)


@pytest.mark.parametrize(
    ("amount", "refusal"),
    [
        (0, tips.TipRefusal.BELOW_MIN),
        (-3, tips.TipRefusal.BELOW_MIN),
        (51, tips.TipRefusal.INSUFFICIENT),
    ],
)
def test_refused_tip_moves_nothing(session: Session, amount: int, refusal: tips.TipRefusal) -> None:
    alice, bob = _seed(session)

    with pytest.raises(tips.TipRefusedError) as refused:
        tips.tip(session, alice, bob, amount)
    session.flush()

    assert refused.value.refusal is refusal
    assert (alice.currency, bob.currency) == (50, 10)
    assert _tip_rows(session) == []


def test_tipping_yourself_is_refused(session: Session) -> None:
    alice, _ = _seed(session)

    with pytest.raises(tips.TipRefusedError) as refused:
        tips.tip(session, alice, alice, 5)

    assert refused.value.refusal is tips.TipRefusal.SELF
    assert alice.currency == 50
    assert _tip_rows(session) == []
