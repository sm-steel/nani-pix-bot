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
    assert shop._discounted(session, bot_tagged_game, 10) == 3
    assert shop._discounted(session, bot_tagged_game, 1) == 1
