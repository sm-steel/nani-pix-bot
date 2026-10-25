import pytest

from nani_pix_bot.models.enums import ConsumableKind
from nani_pix_bot.models.player import Player
from nani_pix_bot.services.cosmetics import consumables

TOKEN = ConsumableKind.CLUE_TICKET


@pytest.fixture(autouse=True)
def alice(session) -> None:
    session.add(Player(telegram_user_id=1))
    session.flush()


def test_nobody_starts_with_tokens(session) -> None:
    assert consumables.count(session, 1, TOKEN) == 0


def test_add_and_take(session) -> None:
    assert consumables.add(session, 1, TOKEN, "test", 2) == 2
    assert consumables.take(session, 1, TOKEN) is True
    assert consumables.count(session, 1, TOKEN) == 1


def test_take_with_none_left_is_false(session) -> None:
    assert consumables.take(session, 1, TOKEN) is False


def test_adding_logs_why(session, log_records) -> None:
    consumables.add(session, 1, TOKEN, why="pass tier 3")
    line = next(r for r in log_records if r.level == "INFO")
    assert "pass tier 3" in line.message
    assert line.extra["recipient_id"] == 1


def test_holdings_lists_every_kind_with_zeros(session) -> None:
    consumables.add(session, 1, ConsumableKind.OMAMORI, why="test")
    assert consumables.holdings(session, 1) == {
        ConsumableKind.CLUE_TICKET: 0,
        ConsumableKind.OFUDA: 0,
        ConsumableKind.OMAMORI: 1,
    }
