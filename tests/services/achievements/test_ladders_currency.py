from nani_pix_bot.models.enums import EventType
from nani_pix_bot.services.achievements import ladders
from tests.services.achievements.fake_history import FakeHistory, ev


def _moved(amount: int, **data):
    return ev(
        EventType.CURRENCY_MOVED,
        None,
        1,
        amount=amount,
        reason="win",
        reversal=False,
        **data,
    )


def test_earned_counts_base_currency_only() -> None:
    history = FakeHistory(
        1,
        [
            _moved(10, currency="base"),
            _moved(30, currency="season", season_id=1),
            _moved(4),  # logged before currencies existed: base
        ],
    )
    assert ladders.earned(history) == 14
