"""Issue #446: a screenshot handed out in a game is never sold again in it,
to anyone, refund or not."""

import pytest
from sqlalchemy.orm import Session

from nani_pix_bot.models import CluePurchase, ClueScreenshotHandout, Player
from nani_pix_bot.models.enums import ClueKind, ConsumableKind, GameStatus, PixelStage
from nani_pix_bot.models.game import Game
from nani_pix_bot.services.clues import handouts, shop
from nani_pix_bot.services.cosmetics import consumables

STARTER, ALICE, BOB = 1, 2, 3


def _setup(session: Session) -> tuple[Game, Player, Player]:
    alice = Player(telegram_user_id=ALICE, currency=1000)
    bob = Player(telegram_user_id=BOB, currency=1000)
    session.add_all([Player(telegram_user_id=STARTER), alice, bob])
    session.flush()
    game = Game(
        starter_id=STARTER,
        status=GameStatus.ACTIVE,
        current_stage=PixelStage.STAGE_1,
        title_romaji="Sousou no Frieren",
        shikimori_id=52991,
    )
    session.add(game)
    session.flush()
    return game, alice, bob


def _screenshot(url: str, fingerprint: int | None = 42) -> shop.PurchaseRequest:
    return shop.PurchaseRequest(
        ClueKind.SCREENSHOT, screenshot_url=url, screenshot_fingerprint=fingerprint
    )


def test_a_screenshot_purchase_records_a_handout(session: Session) -> None:
    game, alice, _ = _setup(session)

    shop.purchase(session, game, alice, _screenshot("https://x/1.jpg", 7))

    [row] = handouts.for_game(session, game.id)
    assert (row.screenshot_url, row.fingerprint) == ("https://x/1.jpg", 7)


def test_a_token_paid_screenshot_records_a_handout(session: Session) -> None:
    game, alice, _ = _setup(session)
    consumables.add(session, ALICE, ConsumableKind.CLUE_TICKET, why="test")

    bought = shop.purchase(session, game, alice, _screenshot("https://x/1.jpg"))

    assert bought.token_used
    assert [r.screenshot_url for r in handouts.for_game(session, game.id)] == ["https://x/1.jpg"]


def test_text_clues_record_no_handout(session: Session) -> None:
    game, alice, _ = _setup(session)

    shop.purchase(session, game, alice, shop.PurchaseRequest(ClueKind.LAST_LETTER))

    assert handouts.for_game(session, game.id) == []


def test_another_player_cannot_buy_a_screenshot_already_handed_out(session: Session) -> None:
    game, alice, bob = _setup(session)
    shop.purchase(session, game, alice, _screenshot("https://x/1.jpg"))

    with pytest.raises(shop.ShopRefusedError) as refused:
        shop.purchase(session, game, bob, _screenshot("https://x/1.jpg"))

    assert refused.value.refusal is shop.Refusal.UNAVAILABLE
    assert bob.currency == 1000


def test_a_refund_keeps_the_handout(session: Session) -> None:
    game, alice, _ = _setup(session)
    bought = shop.purchase(session, game, alice, _screenshot("https://x/1.jpg"))

    shop.refund(session, bought)
    session.flush()

    assert session.query(CluePurchase).count() == 0
    assert handouts.is_handed_out(session, game.id, "https://x/1.jpg")
    with pytest.raises(shop.ShopRefusedError):
        shop.purchase(session, game, alice, _screenshot("https://x/1.jpg"))


def test_handouts_are_per_game(session: Session) -> None:
    game, alice, _ = _setup(session)
    other = Game(starter_id=STARTER, status=GameStatus.WON, shikimori_id=52991)
    session.add(other)
    session.flush()
    session.add(ClueScreenshotHandout(game_id=other.id, screenshot_url="https://x/1.jpg"))
    session.flush()

    shop.purchase(session, game, alice, _screenshot("https://x/1.jpg"))

    assert handouts.is_handed_out(session, game.id, "https://x/1.jpg")
