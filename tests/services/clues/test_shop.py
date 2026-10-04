import pytest
from sqlalchemy.orm import Session

from nani_pix_bot.models import CluePurchase, CurrencyTransfer, Player
from nani_pix_bot.models.enums import ClueKind, GameStatus, PixelStage
from nani_pix_bot.models.game import Game
from nani_pix_bot.services.clues import shop
from nani_pix_bot.services.economy import wallet

STARTER, BUYER = 1, 2


def _setup(session: Session, *, currency: int = 100, **game_fields) -> tuple[Game, Player]:
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
        ClueKind.LAST_LETTER: 10,
        ClueKind.FIRST_LETTER: 20,
        ClueKind.TITLE_SHAPE: 25,
        ClueKind.SCREENSHOT: 30,
        ClueKind.TILE: 10,
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
    game, buyer = _setup(session, currency=15)

    affordable = {o.kind: o.affordable for o in shop.offers(session, game, buyer, "en")}

    assert affordable[ClueKind.LAST_LETTER] is True
    assert affordable[ClueKind.FIRST_LETTER] is False


def test_purchase_charges_and_records(session: Session) -> None:
    game, buyer = _setup(session)

    bought = shop.purchase(session, game, buyer, shop.PurchaseRequest(ClueKind.FIRST_LETTER))

    assert buyer.currency == 80
    charge = session.get(CurrencyTransfer, bought.transfer_id)
    assert charge is not None
    assert charge.amount == 20
    assert charge.to_type == "house"
    assert shop.owned_kinds(session, game.id, BUYER) == {ClueKind.FIRST_LETTER}


def test_purchase_rejects_already_owned_kind(session: Session) -> None:
    game, buyer = _setup(session)
    shop.purchase(session, game, buyer, shop.PurchaseRequest(ClueKind.LAST_LETTER))

    with pytest.raises(shop.ShopRefusedError) as refused:
        shop.purchase(session, game, buyer, shop.PurchaseRequest(ClueKind.LAST_LETTER))

    assert refused.value.refusal is shop.Refusal.ALREADY_OWNED
    assert buyer.currency == 90


def test_purchase_rejects_the_setter(session: Session) -> None:
    game, _ = _setup(session)
    setter = session.get(Player, STARTER)
    assert setter is not None
    setter.currency = 100

    with pytest.raises(shop.ShopRefusedError) as refused:
        shop.purchase(session, game, setter, shop.PurchaseRequest(ClueKind.LAST_LETTER))

    assert refused.value.refusal is shop.Refusal.SETTER


def test_purchase_rejects_insufficient_funds_without_charging(session: Session) -> None:
    game, buyer = _setup(session, currency=5)

    with pytest.raises(shop.ShopRefusedError) as refused:
        shop.purchase(session, game, buyer, shop.PurchaseRequest(ClueKind.LAST_LETTER))

    assert refused.value.refusal is shop.Refusal.INSUFFICIENT
    assert buyer.currency == 5
    assert session.query(CluePurchase).count() == 0


def test_screenshot_price_escalates_and_caps_at_three(session: Session) -> None:
    game, buyer = _setup(session, currency=500)
    prices = []
    for n in range(3):
        prices.append(shop.price(session, game, buyer, ClueKind.SCREENSHOT))
        shop.purchase(
            session,
            game,
            buyer,
            shop.PurchaseRequest(ClueKind.SCREENSHOT, screenshot_url=f"https://x/{n}.jpg"),
        )

    assert prices == [30, 45, 60]
    assert ClueKind.SCREENSHOT not in {o.kind for o in shop.offers(session, game, buyer, "en")}
    with pytest.raises(shop.ShopRefusedError) as refused:
        shop.purchase(
            session,
            game,
            buyer,
            shop.PurchaseRequest(ClueKind.SCREENSHOT, screenshot_url="https://x/9.jpg"),
        )
    assert refused.value.refusal is shop.Refusal.UNAVAILABLE


def test_tiles_are_bought_once_each(session: Session) -> None:
    game, buyer = _setup(session)
    shop.purchase(session, game, buyer, shop.PurchaseRequest(ClueKind.TILE, tile_index=7))

    with pytest.raises(shop.ShopRefusedError) as refused:
        shop.purchase(session, game, buyer, shop.PurchaseRequest(ClueKind.TILE, tile_index=7))

    assert refused.value.refusal is shop.Refusal.ALREADY_OWNED
    shop.purchase(session, game, buyer, shop.PurchaseRequest(ClueKind.TILE, tile_index=8))
    assert shop.owned_tiles(session, game.id, BUYER) == {7, 8}


def test_refund_restores_balance_links_the_charge_and_drops_the_purchase(
    session: Session,
) -> None:
    game, buyer = _setup(session)
    bought = shop.purchase(session, game, buyer, shop.PurchaseRequest(ClueKind.TITLE_SHAPE))
    charge_id = bought.transfer_id

    shop.refund(session, bought)
    session.flush()

    assert buyer.currency == 100
    refund_row = session.query(CurrencyTransfer).filter_by(reverses_id=charge_id).one()
    assert refund_row.amount == 25
    assert session.query(CluePurchase).count() == 0
    # opening 100 was seeded directly, not via the ledger
    assert buyer.currency == wallet.ledger_balance(session, BUYER) + 100


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
