from datetime import UTC, datetime
from zoneinfo import ZoneInfo

from nani_pix_bot.services.seasons.when import parse_local

MSK = ZoneInfo("Europe/Moscow")


def test_parses_local_time_to_utc() -> None:
    assert parse_local("2026-11-20", "18:00", MSK) == datetime(2026, 11, 20, 15, 0, tzinfo=UTC)


def test_dst_is_respected() -> None:
    berlin = ZoneInfo("Europe/Berlin")
    assert parse_local("2026-07-01", "12:00", berlin) == datetime(2026, 7, 1, 10, 0, tzinfo=UTC)
    assert parse_local("2026-12-01", "12:00", berlin) == datetime(2026, 12, 1, 11, 0, tzinfo=UTC)


def test_rejects_garbage() -> None:
    for date_text, time_text in [
        ("2026-13-01", "10:00"),
        ("20.11.2026", "10:00"),
        ("2026-11-31", "10:00"),
        ("2026-11-20", "25:00"),
    ]:
        assert parse_local(date_text, time_text, MSK) is None
