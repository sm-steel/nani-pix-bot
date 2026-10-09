import pytest

from nani_pix_bot.commands.helpers.durations import clock


@pytest.mark.parametrize(
    ("seconds", "shown"),
    [
        (0, "0:00"),
        (5, "0:05"),
        (65.9, "1:05"),  # whole seconds, rounded down
        (600, "10:00"),
        (3599, "59:59"),
        (3600, "1:00:00"),
        (3900, "1:05:00"),
        (90061, "25:01:01"),
    ],
)
def test_clock_is_m_ss_under_an_hour_and_h_mm_ss_from_one(seconds: float, shown: str) -> None:
    assert clock(seconds) == shown
