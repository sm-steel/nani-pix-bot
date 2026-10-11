"""Clue purchases paid with a clue token (seasons spec §3) instead of 💠: the
spend and the refund that gives the token back. The price path, the menu and
the currency-paid purchases stay in shop.py."""

from typing import TYPE_CHECKING

from loguru import logger
from sqlalchemy.orm import Session

from nani_pix_bot.models.clue_purchase import CluePurchase
from nani_pix_bot.models.enums import ClueKind, ConsumableKind, EventType
from nani_pix_bot.models.game import Game
from nani_pix_bot.models.player import Player
from nani_pix_bot.services import events
from nani_pix_bot.services.cosmetics import consumables

if TYPE_CHECKING:
    from nani_pix_bot.services.clues.shop import PurchaseRequest


def pay(
    session: Session,
    game: Game,
    buyer: Player,
    request: "PurchaseRequest",
    detail: dict[str, object],
) -> CluePurchase | None:
    """Spend one of the buyer's clue tokens on the clue; None if they hold none.
    `detail` is shop._detail(request), the purchase line's log kwargs."""
    if not consumables.take(session, buyer.telegram_user_id, ConsumableKind.CLUE_TICKET):
        return None
    bought = CluePurchase(
        game_id=game.id,
        player_id=buyer.telegram_user_id,
        kind=request.kind,
        tile_index=request.tile_index,
        screenshot_url=request.screenshot_url,
        token_used=True,
    )
    session.add(bought)
    session.flush()
    logger.info(
        "bought the {kind} clue{detail} with a clue token (purchase {purchase_id})",
        purchase_id=bought.id,
        game_id=game.id,
        **detail,
    )
    return bought


def refund(session: Session, purchase_row: CluePurchase) -> int:
    """Give the clue token back and drop the row; nothing was charged, so 0 💠."""
    consumables.add(
        session,
        purchase_row.player_id,
        ConsumableKind.CLUE_TICKET,
        why=f"refund of clue purchase {purchase_row.id}",
    )
    session.delete(purchase_row)
    logger.info(
        "refunded {kind} clue purchase {purchase_id}: the clue token is back",
        kind=ClueKind(purchase_row.kind).value,
        purchase_id=purchase_row.id,
        game_id=purchase_row.game_id,
    )
    emit_refunded(session, purchase_row)
    return 0


def emit_refunded(session: Session, purchase_row: CluePurchase) -> None:
    events.emit(
        session,
        EventType.CLUE_REFUNDED,
        events.Involved(actor_id=purchase_row.player_id, game_id=purchase_row.game_id),
        kind=ClueKind(purchase_row.kind).value,
    )
