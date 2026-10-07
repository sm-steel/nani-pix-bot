from sqlalchemy import select
from sqlalchemy.orm import Session

from nani_pix_bot.models.enums import CurrencyReason, EventType, GameStatus, PixelStage
from nani_pix_bot.models.event_log import EventLog
from nani_pix_bot.models.game import Game
from nani_pix_bot.models.player import Player
from nani_pix_bot.services.economy import bounty, wallet

ALICE, BOB, CAROL = 2, 3, 4


def _players(session: Session) -> dict[int, Player]:
    players = {u: Player(telegram_user_id=u, currency=1000) for u in (1, ALICE, BOB, CAROL)}
    session.add_all(players.values())
    session.flush()
    return players


def _events(session: Session, event_type: EventType) -> list[EventLog]:
    stmt = select(EventLog).where(EventLog.event_type == event_type).order_by(EventLog.id)
    return list(session.scalars(stmt))


def test_a_tip_logs_payer_as_actor_and_recipient_as_subject(session: Session) -> None:
    p = _players(session)

    wallet.transfer(
        session,
        wallet.Party.of(p[ALICE]),
        wallet.Party.of(p[BOB]),
        5,
        wallet.LedgerEntry(CurrencyReason.TIP),
    )

    (row,) = _events(session, EventType.CURRENCY_MOVED)
    assert (row.actor_id, row.subject_id) == (ALICE, BOB)
    assert row.data == {
        "amount": 5,
        "reason": "tip",
        "from_type": "player",
        "to_type": "player",
        "reversal": False,
    }


def test_refunds_and_cashback_are_reversals(session: Session) -> None:
    p = _players(session)
    charge = wallet.debit(session, p[ALICE], 60, wallet.LedgerEntry(CurrencyReason.CLUE_PURCHASE))
    session.flush()

    wallet.credit(
        session, p[ALICE], 60, wallet.LedgerEntry(CurrencyReason.REFUND, reverses_id=charge.id)
    )
    wallet.credit(session, p[ALICE], 30, wallet.LedgerEntry(CurrencyReason.CASHBACK))

    flags = [row.data["reversal"] for row in _events(session, EventType.CURRENCY_MOVED)]
    assert flags == [False, True, True]


def test_a_paid_out_pot_settles_each_contribution(session: Session) -> None:
    p = _players(session)
    game = Game(starter_id=1, status=GameStatus.ACTIVE, current_stage=PixelStage.STAGE_1)
    session.add(game)
    session.flush()
    bounty.contribute(session, game, p[ALICE], 40)
    bounty.contribute(session, game, p[BOB], 30)

    bounty.pay_out(session, game, p[CAROL])

    settled = _events(session, EventType.BOUNTY_SETTLED)
    assert [(e.actor_id, e.subject_id, e.data["amount"]) for e in settled] == [
        (ALICE, CAROL, 40),
        (BOB, CAROL, 30),
    ]


def test_a_refunded_pot_settles_nothing(session: Session) -> None:
    p = _players(session)
    game = Game(starter_id=1, status=GameStatus.ACTIVE, current_stage=PixelStage.STAGE_1)
    session.add(game)
    session.flush()
    bounty.contribute(session, game, p[ALICE], 40)

    bounty.refund_pot(session, game.id)

    assert _events(session, EventType.BOUNTY_SETTLED) == []
