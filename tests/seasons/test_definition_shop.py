from dataclasses import replace

from nani_pix_bot.seasons import registry
from nani_pix_bot.seasons.definition import (
    MAX_SHOP_CLUE_TOKENS,
    RewardKind,
    ShopOffer,
    run_problems,
    shop_problems,
)


def _demo():
    return registry.discover("tests.seasons.fake_runs")["demo_1"]


def test_the_demo_shop_is_valid() -> None:
    assert shop_problems(_demo()) == []
    assert run_problems(_demo()) == []


def test_offer_keys() -> None:
    assert ShopOffer.item("name_flair.blossom", 40).key == "name_flair.blossom"
    assert ShopOffer.clue_ticket(30, 2).key == "clue_token"
    assert ShopOffer.clue_ticket(30, 2).kind is RewardKind.CLUE_TICKET


def test_an_unknown_item_is_a_problem() -> None:
    run = replace(_demo(), shop=(ShopOffer.item("name_flair.nope", 40),))
    assert any("name_flair.nope" in p for p in shop_problems(run))


def test_a_price_must_be_positive() -> None:
    run = replace(_demo(), shop=(ShopOffer.item("name_flair.blossom", 0),))
    assert shop_problems(run)


def test_an_item_sells_once() -> None:
    offer = replace(ShopOffer.item("name_flair.blossom", 40), limit=2)
    assert shop_problems(replace(_demo(), shop=(offer,)))


def test_the_clue_token_limit_is_capped() -> None:
    too_many = ShopOffer.clue_ticket(30, MAX_SHOP_CLUE_TOKENS + 1)
    assert shop_problems(replace(_demo(), shop=(too_many,)))
    assert shop_problems(replace(_demo(), shop=(ShopOffer.clue_ticket(30, 0),)))


def test_an_offer_appears_once() -> None:
    offer = ShopOffer.item("name_flair.blossom", 40)
    assert shop_problems(replace(_demo(), shop=(offer, offer)))


def test_coins_and_currency_are_not_for_sale() -> None:
    offer = ShopOffer(RewardKind.COINS, price=10)
    assert shop_problems(replace(_demo(), shop=(offer,)))


def test_a_pass_reward_is_not_also_sold() -> None:
    for key in ("name_flair.blossom", "guess_reaction.love"):  # a free and a premium reward
        run = replace(_demo(), shop=(ShopOffer.item(key, 40),))
        assert any(key in p and "pass reward" in p for p in shop_problems(run)), key
