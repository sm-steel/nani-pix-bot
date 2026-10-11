import pytest

from nani_pix_bot.models.enums import ClueKind
from nani_pix_bot.services.clues import shop
from nani_pix_bot.services.economy import config


@pytest.fixture(autouse=True)
def _season_runs(fake_runs):
    """The shared fake runs, for this module only."""


def _base(session, kind=ClueKind.LAST_LETTER) -> int:
    return config.get_amounts(session)[shop._PRICE_KEYS[kind]]


def test_a_season_game_gets_the_season_discount(session, tagged_game, buyer) -> None:
    base = _base(session)
    assert shop.price(session, tagged_game, buyer, ClueKind.LAST_LETTER) == max(1, base * 75 // 100)


def test_an_untagged_game_pays_full_price(session, untagged_game, buyer) -> None:
    assert shop.price(session, untagged_game, buyer, ClueKind.LAST_LETTER) == _base(session)


def test_the_season_discount_stacks_with_hard_mode(session, bot_tagged_game) -> None:
    bot_tagged_game.hard_mode_clue_discount = 50
    assert shop._quote(session, bot_tagged_game, 10).cost == 3
    assert shop._quote(session, bot_tagged_game, 1).cost == 1


def test_a_discounted_purchase_logs_the_discount(
    session, bot_tagged_game, buyer, log_records
) -> None:
    bot_tagged_game.hard_mode_clue_discount = 50
    buyer.currency = 1000
    base = _base(session)

    shop.purchase(session, bot_tagged_game, buyer, shop.PurchaseRequest(ClueKind.LAST_LETTER))

    [line] = [r for r in log_records if r.level == "INFO" and "bought the" in r.message]
    assert line.extra["base_price"] == base
    assert line.extra["hard_mode_pct"] == 50
    assert line.extra["season_pct"] == 25
    assert f"discounted from {base}" in line.message


def test_a_full_price_purchase_logs_no_discount(session, untagged_game, buyer, log_records) -> None:
    buyer.currency = 1000

    shop.purchase(session, untagged_game, buyer, shop.PurchaseRequest(ClueKind.LAST_LETTER))

    [line] = [r for r in log_records if r.level == "INFO" and "bought the" in r.message]
    assert "base_price" not in line.extra
    assert "discounted" not in line.message
