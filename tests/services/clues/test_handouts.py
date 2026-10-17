"""The original of every handed-out screenshot clue is kept for the
end-of-game album, then cleared."""

from sqlalchemy.orm import Session

from nani_pix_bot.models import CluePurchase, ClueScreenshotHandout, Player
from nani_pix_bot.models.enums import ClueKind, GameStatus, PixelStage
from nani_pix_bot.models.game import Game
from nani_pix_bot.services.clues import handouts, shop

STARTER, ALICE = 1, 2


def _setup(session: Session) -> tuple[Game, Player]:
    alice = Player(telegram_user_id=ALICE, currency=1000)
    session.add_all([Player(telegram_user_id=STARTER), alice])
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
    return game, alice


def _buy(
    session: Session, game: Game, player: Player, name: str, original: bytes | None
) -> CluePurchase:
    request = shop.PurchaseRequest(
        ClueKind.SCREENSHOT,
        screenshot_url=f"https://x/{name}.jpg",
        screenshot_fingerprint=1,
        screenshot_original=original,
    )
    return shop.purchase(session, game, player, request)


def test_record_stores_original(session: Session) -> None:
    game, alice = _setup(session)

    _buy(session, game, alice, "a", b"orig")

    assert handouts.revealed_images(session, game.id) == [b"orig"]


def test_revealed_images_in_handout_order(session: Session) -> None:
    game, alice = _setup(session)

    for name in ("a", "b", "c"):
        _buy(session, game, alice, name, name.encode())

    assert handouts.revealed_images(session, game.id) == [b"a", b"b", b"c"]


def test_revealed_images_include_refunded(session: Session) -> None:
    game, alice = _setup(session)
    bought = _buy(session, game, alice, "a", b"orig")

    shop.refund(session, bought)

    assert handouts.revealed_images(session, game.id) == [b"orig"]


def test_revealed_images_skip_null(session: Session) -> None:
    game, alice = _setup(session)
    session.add(ClueScreenshotHandout(game_id=game.id, screenshot_url="https://x/old.jpg"))
    session.flush()
    _buy(session, game, alice, "a", b"orig")

    assert handouts.revealed_images(session, game.id) == [b"orig"]


def test_clear_images(session: Session) -> None:
    game, alice = _setup(session)
    _buy(session, game, alice, "a", b"a")
    _buy(session, game, alice, "b", b"b")

    assert handouts.clear_images(session, game.id) == 2

    assert handouts.revealed_images(session, game.id) == []
    assert len(handouts.for_game(session, game.id)) == 2
