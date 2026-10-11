from datetime import timedelta

import pytest
from sqlalchemy import select

from nani_pix_bot.models.enums import EventType, SeasonStatus
from nani_pix_bot.models.event_log import EventLog
from nani_pix_bot.models.player import Player
from nani_pix_bot.models.season import SeasonSchedule
from nani_pix_bot.seasons import registry
from nani_pix_bot.services.economy import wallet
from nani_pix_bot.services.seasons import conversion, lifecycle
from tests.services.seasons.conftest import NOW


def _coins(season_id: int) -> wallet.LedgerEntry:
    return wallet.LedgerEntry(wallet.CurrencyReason.GRANT, season_id=season_id, coins=True)


@pytest.fixture
def season(active_season) -> SeasonSchedule:
    return active_season


@pytest.fixture
def demo():
    return registry.get("demo_1")


@pytest.fixture
def alice(session) -> Player:
    player = Player(telegram_user_id=1)
    session.add(player)
    session.flush()
    return player


@pytest.fixture
def closing_season(session) -> SeasonSchedule:
    row = SeasonSchedule(
        run_id="demo_1",
        start_at=NOW - timedelta(days=3),
        end_at=NOW - timedelta(days=1),
        status=SeasonStatus.ACTIVE,
        created_by=9,
    )
    session.add(row)
    session.flush()
    return row


def test_leftover_coins_become_currency_at_the_runs_rate(session, season, demo, alice) -> None:
    wallet.credit(session, alice, 45, _coins(season.id))
    before = alice.currency
    done = conversion.convert(session, season, demo)
    assert alice.currency == before + 22  # 45 🪙 at 2:1, the odd coin dropped
    assert wallet.balance(session, 1, season_id=season.id) == 0
    assert done == conversion.Converted(players=1, coins=45, currency=22)


def test_a_balance_too_small_to_convert_is_still_emptied(session, season, demo, alice) -> None:
    wallet.credit(session, alice, 1, _coins(season.id))
    conversion.convert(session, season, demo)
    assert wallet.balance(session, 1, season_id=season.id) == 0


def test_converting_twice_converts_nothing_more(session, season, demo, alice) -> None:
    wallet.credit(session, alice, 10, _coins(season.id))
    conversion.convert(session, season, demo)
    assert conversion.convert(session, season, demo) == conversion.Converted(0, 0, 0)


def test_finalizing_a_season_converts_its_coins(session, closing_season, alice) -> None:
    wallet.credit(session, alice, 10, _coins(closing_season.id))
    lifecycle.advance(session, NOW)  # no running games: finalizes
    assert wallet.balance(session, 1, season_id=closing_season.id) == 0
    assert conversion.summary(session, closing_season.id).currency == 5


def test_an_unloadable_run_logs_an_error_instead_of_crashing(
    session, closing_season, alice, monkeypatch, log_records
) -> None:
    wallet.credit(session, alice, 10, _coins(closing_season.id))
    monkeypatch.setattr(registry, "get", lambda run_id: None)
    lifecycle.advance(session, NOW)
    assert closing_season.status == SeasonStatus.ENDED
    assert any(r.level == "ERROR" and "unloadable run" in r.message for r in log_records)


def test_converted_currency_is_logged_as_base_currency(session, season, demo, alice) -> None:
    """Converted 💠 is real 💠 earned: it counts toward 💠 achievements like any
    other credit (the 🪙 side never did)."""
    wallet.credit(session, alice, 10, _coins(season.id))
    conversion.convert(session, season, demo)
    moved = session.scalars(
        select(EventLog).where(EventLog.event_type == EventType.CURRENCY_MOVED)
    ).all()
    credit = moved[-1].data
    assert (credit["reason"], credit["currency"], credit["amount"]) == (
        "season_conversion",
        "base",
        5,
    )
