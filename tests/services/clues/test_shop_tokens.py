import pytest
from sqlalchemy.orm import Session

from nani_pix_bot.models import Player
from nani_pix_bot.models.enums import ClueKind, ConsumableKind, GameStatus, PixelStage
from nani_pix_bot.models.game import Game
from nani_pix_bot.services.clues import shop
from nani_pix_bot.services.clues.shop import PurchaseRequest
from nani_pix_bot.services.cosmetics import consumables
from nani_pix_bot.services.economy import earning

TOKEN = ConsumableKind.CLUE_TICKET


@pytest.fixture
def buyer(session: Session) -> Player:
    buyer = Player(telegram_user_id=2, currency=1000)
    session.add_all([Player(telegram_user_id=1), buyer])
    session.flush()
    return buyer


@pytest.fixture
def game(session: Session, buyer: Player) -> Game:
    game = Game(
        starter_id=1,
        status=GameStatus.ACTIVE,
        current_stage=PixelStage.STAGE_1,
        title_romaji="Sousou no Frieren",
        shikimori_id=52991,
    )
    session.add(game)
    session.flush()
    return game


def _give(session: Session, buyer: Player) -> None:
    consumables.add(session, buyer.telegram_user_id, TOKEN, why="test")


def test_a_token_pays_for_the_next_clue(session, game, buyer) -> None:
    _give(session, buyer)
    before = buyer.currency
    bought = shop.purchase(session, game, buyer, PurchaseRequest(ClueKind.FIRST_LETTER))
    assert bought.token_used is True
    assert bought.transfer_id is None
    assert buyer.currency == before
    assert consumables.count(session, buyer.telegram_user_id, TOKEN) == 0


def test_without_a_token_the_clue_costs_currency_as_before(session, game, buyer) -> None:
    before = buyer.currency
    bought = shop.purchase(session, game, buyer, PurchaseRequest(ClueKind.FIRST_LETTER))
    assert bought.token_used is False
    assert buyer.currency < before


def test_offers_show_the_token(session, game, buyer) -> None:
    _give(session, buyer)
    assert all(o.token and o.affordable for o in shop.offers(session, game, buyer, "en"))


def test_refunding_a_token_purchase_returns_the_token(session, game, buyer) -> None:
    _give(session, buyer)
    before = buyer.currency
    bought = shop.purchase(session, game, buyer, PurchaseRequest(ClueKind.FIRST_LETTER))
    assert shop.refund(session, bought) == 0
    assert consumables.count(session, buyer.telegram_user_id, TOKEN) == 1
    assert buyer.currency == before


def test_a_token_purchase_is_not_refundable_by_an_admin(session, game, buyer) -> None:
    _give(session, buyer)
    shop.purchase(session, game, buyer, PurchaseRequest(ClueKind.FIRST_LETTER))
    assert shop.refundable_purchases(session, buyer.telegram_user_id) == []


def test_a_token_purchase_adds_nothing_to_the_clue_spend(session, game, buyer) -> None:
    _give(session, buyer)
    shop.purchase(session, game, buyer, PurchaseRequest(ClueKind.FIRST_LETTER))
    assert earning.clue_spend(session, game.id) == {}


def test_a_token_purchase_leaves_its_buyer_off_the_refund_list(session, game, buyer) -> None:
    _give(session, buyer)
    shop.purchase(session, game, buyer, PurchaseRequest(ClueKind.FIRST_LETTER))
    assert shop.refundable_player_ids(session) == []
