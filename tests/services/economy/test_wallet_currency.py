from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select

from nani_pix_bot.models.currency_transfer import CurrencyTransfer
from nani_pix_bot.models.enums import Currency, CurrencyReason, EventType, SeasonStatus
from nani_pix_bot.models.event_log import EventLog
from nani_pix_bot.models.player import Player
from nani_pix_bot.models.season import SeasonBalance, SeasonSchedule
from nani_pix_bot.services.economy import wallet

NOW = datetime(2026, 1, 1, tzinfo=UTC)


@pytest.fixture
def season_id(session) -> int:
    row = SeasonSchedule(
        run_id="demo_1",
        start_at=NOW,
        end_at=NOW + timedelta(days=5),
        status=SeasonStatus.ACTIVE,
        created_by=9,
    )
    session.add(row)
    session.flush()
    return row.id


@pytest.fixture
def alice(session) -> Player:
    player = Player(telegram_user_id=1, username="alice", currency=100)
    session.add(player)
    session.flush()
    return player


def _coins(season_id: int, reason=CurrencyReason.GRANT) -> wallet.LedgerEntry:
    return wallet.LedgerEntry(reason, season_id=season_id, coins=True)


def test_coins_go_to_the_season_balance_not_the_currency(session, alice, season_id) -> None:
    wallet.credit(session, alice, 30, _coins(season_id))
    assert alice.currency == 100
    assert session.get(SeasonBalance, (season_id, 1)).coins == 30
    assert wallet.balance(session, 1, season_id=season_id) == 30
    assert wallet.balance(session, 1) == 100


def test_a_coin_transfer_is_recorded_as_season_currency(session, alice, season_id) -> None:
    wallet.credit(session, alice, 30, _coins(season_id))
    row = session.scalars(select(CurrencyTransfer)).one()
    assert (row.currency, row.season_id) == (Currency.SEASON, season_id)


def test_spending_more_coins_than_held_is_refused(session, alice, season_id) -> None:
    wallet.credit(session, alice, 10, _coins(season_id))
    with pytest.raises(wallet.InsufficientCurrencyError):
        wallet.debit(session, alice, 11, _coins(season_id))
    assert wallet.balance(session, 1, season_id=season_id) == 10


def test_coins_of_one_season_are_not_another_seasons(session, alice, season_id) -> None:
    other = SeasonSchedule(
        run_id="other_1", start_at=NOW, end_at=NOW, status=SeasonStatus.ENDED, created_by=9
    )
    session.add(other)
    session.flush()
    wallet.credit(session, alice, 10, _coins(season_id))
    assert wallet.balance(session, 1, season_id=other.id) == 0


def test_coins_need_a_season() -> None:
    with pytest.raises(ValueError, match="season"):
        wallet.LedgerEntry(CurrencyReason.GRANT, coins=True)


def test_a_base_transfer_may_name_its_season(session, alice, season_id) -> None:
    """e.g. buying Premium: 💠 that belongs to a season's story."""
    wallet.debit(session, alice, 50, wallet.LedgerEntry(CurrencyReason.GRANT, season_id=season_id))
    row = session.scalars(select(CurrencyTransfer)).one()
    assert (row.currency, row.season_id) == (Currency.BASE, season_id)
    assert alice.currency == 50


def test_the_event_says_which_currency_moved(session, alice, season_id) -> None:
    wallet.credit(session, alice, 30, _coins(season_id))
    wallet.credit(session, alice, 5, wallet.LedgerEntry(CurrencyReason.GRANT))
    moved = session.scalars(
        select(EventLog).where(EventLog.event_type == EventType.CURRENCY_MOVED)
    ).all()
    assert [(e.data["currency"], e.data["season_id"]) for e in moved] == [
        ("season", season_id),
        ("base", None),
    ]
