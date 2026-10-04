import pytest
from sqlalchemy.orm import Session

from nani_pix_bot.models import CluePurchase, CurrencyTransfer, Player
from nani_pix_bot.models.enums import ClueKind, GameStatus, PixelStage
from nani_pix_bot.models.game import Game
from nani_pix_bot.services.clues import shop
from nani_pix_bot.services.economy import config, wallet
from nani_pix_bot.services.economy.config import EconomyKey

STARTER, BUYER = 1, 2
START = 1000
PRICES = config.DEFAULT_AMOUNTS


def _setup(session: Session, *, currency: int = START, **game_fields) -> tuple[Game, Player]:
    buyer = Player(telegram_user_id=BUYER, currency=currency)
    session.add_all([Player(telegram_user_id=STARTER), buyer])
    session.flush()
    fields = {
        "starter_id": STARTER,
        "status": GameStatus.ACTIVE,
        "current_stage": PixelStage.STAGE_1,
        "title_romaji": "Sousou no Frieren",
        "shikimori_id": 52991,
    }
    fields.update(game_fields)
    game = Game(**fields)
    session.add(game)
    session.flush()
    return game, buyer


def test_offers_list_all_kinds_with_default_prices(session: Session) -> None:
    game, buyer = _setup(session)

    offers = {o.kind: o for o in shop.offers(session, game, buyer, "en")}

    assert {k: o.price for k, o in offers.items()} == {
        ClueKind.LAST_LETTER: 60,
        ClueKind.FIRST_LETTER: 100,
        ClueKind.TITLE_SHAPE: 120,
        ClueKind.SCREENSHOT: 150,
        ClueKind.TILE: 100,
    }
    assert all(o.affordable for o in offers.values())


def test_offers_hide_text_clues_without_a_title_and_tile_in_hard_mode(session: Session) -> None:
    game, buyer = _setup(
        session, title_romaji=None, hard_mode=True, hard_mode_turn=1, current_stage=None
    )

    kinds = {o.kind for o in shop.offers(session, game, buyer, "en")}

    assert kinds == {ClueKind.SCREENSHOT}


def test_offers_hide_screenshot_without_a_screenshot_provider(session: Session) -> None:
    game, buyer = _setup(session, shikimori_id=None)

    assert ClueKind.SCREENSHOT not in {o.kind for o in shop.offers(session, game, buyer, "en")}


def test_offers_mark_unaffordable(session: Session) -> None:
    game, buyer = _setup(session, currency=PRICES[EconomyKey.CLUE_LAST_LETTER])

    affordable = {o.kind: o.affordable for o in shop.offers(session, game, buyer, "en")}

    assert affordable[ClueKind.LAST_LETTER] is True
    assert affordable[ClueKind.FIRST_LETTER] is False


def test_purchase_charges_and_records(session: Session) -> None:
    game, buyer = _setup(session)

    bought = shop.purchase(session, game, buyer, shop.PurchaseRequest(ClueKind.FIRST_LETTER))

    assert buyer.currency == START - PRICES[EconomyKey.CLUE_FIRST_LETTER]
    charge = session.get(CurrencyTransfer, bought.transfer_id)
    assert charge is not None
    assert charge.amount == PRICES[EconomyKey.CLUE_FIRST_LETTER]
    assert charge.to_type == "house"
    assert shop.owned_kinds(session, game.id, BUYER) == {ClueKind.FIRST_LETTER}


def test_purchase_rejects_already_owned_kind(session: Session) -> None:
    game, buyer = _setup(session)
    shop.purchase(session, game, buyer, shop.PurchaseRequest(ClueKind.LAST_LETTER))

    with pytest.raises(shop.ShopRefusedError) as refused:
        shop.purchase(session, game, buyer, shop.PurchaseRequest(ClueKind.LAST_LETTER))

    assert refused.value.refusal is shop.Refusal.ALREADY_OWNED
    assert buyer.currency == START - PRICES[EconomyKey.CLUE_LAST_LETTER]


def test_purchase_rejects_the_setter(session: Session) -> None:
    game, _ = _setup(session)
    setter = session.get(Player, STARTER)
    assert setter is not None
    setter.currency = 100

    with pytest.raises(shop.ShopRefusedError) as refused:
        shop.purchase(session, game, setter, shop.PurchaseRequest(ClueKind.LAST_LETTER))

    assert refused.value.refusal is shop.Refusal.SETTER


def test_purchase_rejects_insufficient_funds_without_charging(session: Session) -> None:
    game, buyer = _setup(session, currency=PRICES[EconomyKey.CLUE_LAST_LETTER] - 1)

    with pytest.raises(shop.ShopRefusedError) as refused:
        shop.purchase(session, game, buyer, shop.PurchaseRequest(ClueKind.LAST_LETTER))

    assert refused.value.refusal is shop.Refusal.INSUFFICIENT
    assert buyer.currency == PRICES[EconomyKey.CLUE_LAST_LETTER] - 1
    assert session.query(CluePurchase).count() == 0


def test_screenshot_price_escalates_and_caps_at_three(session: Session) -> None:
    game, buyer = _setup(session, currency=START)
    prices = []
    for n in range(3):
        prices.append(shop.price(session, game, buyer, ClueKind.SCREENSHOT))
        shop.purchase(
            session,
            game,
            buyer,
            shop.PurchaseRequest(ClueKind.SCREENSHOT, screenshot_url=f"https://x/{n}.jpg"),
        )

    assert prices == [150, 225, 300]
    assert ClueKind.SCREENSHOT not in {o.kind for o in shop.offers(session, game, buyer, "en")}
    with pytest.raises(shop.ShopRefusedError) as refused:
        shop.purchase(
            session,
            game,
            buyer,
            shop.PurchaseRequest(ClueKind.SCREENSHOT, screenshot_url="https://x/9.jpg"),
        )
    assert refused.value.refusal is shop.Refusal.UNAVAILABLE


def test_round_tile_is_none_until_someone_buys_one(session: Session) -> None:
    game, _ = _setup(session)

    assert shop.round_tile(session, game.id) is None


def test_first_buyer_picks_the_round_tile_and_a_player_buys_it_once(session: Session) -> None:
    game, buyer = _setup(session)
    shop.purchase(session, game, buyer, shop.PurchaseRequest(ClueKind.TILE, tile_index=7))

    assert shop.round_tile(session, game.id) == 7
    for index in (7, 8):
        with pytest.raises(shop.ShopRefusedError) as refused:
            shop.purchase(
                session, game, buyer, shop.PurchaseRequest(ClueKind.TILE, tile_index=index)
            )
        assert refused.value.refusal is shop.Refusal.ALREADY_OWNED
    assert session.query(CluePurchase).count() == 1


def test_later_buyer_gets_the_round_tile_and_a_different_index_is_refused(
    session: Session,
) -> None:
    game, first = _setup(session)
    second = Player(telegram_user_id=3, currency=START)
    session.add(second)
    session.flush()
    shop.purchase(session, game, first, shop.PurchaseRequest(ClueKind.TILE, tile_index=7))

    with pytest.raises(shop.ShopRefusedError) as refused:
        shop.purchase(session, game, second, shop.PurchaseRequest(ClueKind.TILE, tile_index=8))
    assert refused.value.refusal is shop.Refusal.UNAVAILABLE
    assert second.currency == START

    row = shop.purchase(session, game, second, shop.PurchaseRequest(ClueKind.TILE, tile_index=7))
    assert row.tile_index == 7
    assert second.currency == START - shop.price(session, game, second, ClueKind.TILE)


def test_tile_offer_stays_for_others_and_goes_for_the_owner(session: Session) -> None:
    game, buyer = _setup(session)
    other = Player(telegram_user_id=3, currency=START)
    session.add(other)
    session.flush()
    shop.purchase(session, game, buyer, shop.PurchaseRequest(ClueKind.TILE, tile_index=7))

    assert ClueKind.TILE not in {o.kind for o in shop.offers(session, game, buyer, "en")}
    assert ClueKind.TILE in {o.kind for o in shop.offers(session, game, other, "en")}


def test_refund_restores_balance_links_the_charge_and_drops_the_purchase(
    session: Session,
) -> None:
    game, buyer = _setup(session)
    bought = shop.purchase(session, game, buyer, shop.PurchaseRequest(ClueKind.TITLE_SHAPE))
    charge_id = bought.transfer_id

    shop.refund(session, bought)
    session.flush()

    assert buyer.currency == START
    refund_row = session.query(CurrencyTransfer).filter_by(reverses_id=charge_id).one()
    assert refund_row.amount == PRICES[EconomyKey.CLUE_TITLE_SHAPE]
    assert session.query(CluePurchase).count() == 0
    # opening 100 was seeded directly, not via the ledger
    assert buyer.currency == wallet.ledger_balance(session, BUYER) + START


def test_active_game_for_rejects_a_different_or_finished_game(session: Session) -> None:
    game, _ = _setup(session)
    assert shop.active_game_for(session, game.id) is game
    assert shop.active_game_for(session, game.id + 1) is None
    game.status = GameStatus.WON
    assert shop.active_game_for(session, game.id) is None


def test_mark_shared_only_once(session: Session) -> None:
    game, buyer = _setup(session)
    bought = shop.purchase(session, game, buyer, shop.PurchaseRequest(ClueKind.LAST_LETTER))

    assert shop.mark_shared(bought) is True
    assert shop.mark_shared(bought) is False


@pytest.mark.parametrize("index", [None, -1, 64])
def test_purchase_rejects_a_tile_outside_the_grid(session: Session, index) -> None:
    game, buyer = _setup(session, original_image=b"x")

    with pytest.raises(shop.ShopRefusedError) as refused:
        shop.purchase(session, game, buyer, shop.PurchaseRequest(ClueKind.TILE, tile_index=index))

    assert refused.value.refusal is shop.Refusal.UNAVAILABLE
    assert buyer.currency == START


@pytest.mark.parametrize("url", [None, ""])
def test_purchase_rejects_a_screenshot_without_a_url(session: Session, url) -> None:
    game, buyer = _setup(session)

    with pytest.raises(shop.ShopRefusedError) as refused:
        shop.purchase(
            session, game, buyer, shop.PurchaseRequest(ClueKind.SCREENSHOT, screenshot_url=url)
        )

    assert refused.value.refusal is shop.Refusal.UNAVAILABLE
    assert buyer.currency == START


def test_screenshot_offer_carries_the_owned_count(session: Session) -> None:
    game, buyer = _setup(session)
    shop.purchase(
        session,
        game,
        buyer,
        shop.PurchaseRequest(ClueKind.SCREENSHOT, screenshot_url="https://x/0.jpg"),
    )

    offers = {o.kind: o for o in shop.offers(session, game, buyer, "en")}

    assert offers[ClueKind.SCREENSHOT].owned_screenshots == 1
    assert offers[ClueKind.LAST_LETTER].owned_screenshots == 0


def test_refund_game_refunds_every_purchase_of_every_player(session: Session) -> None:
    game, buyer = _setup(session)
    other = Player(telegram_user_id=3, currency=START)
    session.add(other)
    session.flush()
    shop.purchase(session, game, buyer, shop.PurchaseRequest(ClueKind.FIRST_LETTER))
    shop.purchase(session, game, buyer, shop.PurchaseRequest(ClueKind.LAST_LETTER))
    shop.purchase(session, game, other, shop.PurchaseRequest(ClueKind.TITLE_SHAPE))

    refunded = shop.refund_game(session, game.id)
    session.flush()

    assert refunded == 3
    assert (buyer.currency, other.currency) == (START, START)
    assert session.query(CluePurchase).count() == 0
