from sqlalchemy.orm import Session

from nani_pix_bot.models import CluePurchase, Player
from nani_pix_bot.models.enums import ClueKind, CurrencyReason, GameStatus
from nani_pix_bot.models.game import Game
from nani_pix_bot.services.economy import wallet


def test_clue_purchase_round_trips_and_links_its_charge(session: Session) -> None:
    buyer = Player(telegram_user_id=2, currency=50)
    session.add_all([Player(telegram_user_id=1), buyer])
    session.flush()
    game = Game(starter_id=1, status=GameStatus.ACTIVE, shown_screenshot_urls=["https://x/1.jpg"])
    session.add(game)
    session.flush()
    charge = wallet.debit(
        session, buyer, 10, wallet.LedgerEntry(CurrencyReason.CLUE_PURCHASE, game_id=game.id)
    )
    session.flush()
    session.add(
        CluePurchase(
            game_id=game.id,
            player_id=2,
            kind=ClueKind.TILE,
            tile_index=5,
            transfer_id=charge.id,
        )
    )
    session.commit()

    row = session.query(CluePurchase).one()
    assert row.kind == ClueKind.TILE
    assert row.tile_index == 5
    assert row.shared_at is None
    stored = session.get(Game, game.id)
    assert stored is not None
    assert stored.shown_screenshot_urls == ["https://x/1.jpg"]
