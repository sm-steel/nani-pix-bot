"""Clue-shop rules: what a player may buy in the active game, at what
price, and the purchase/refund bookkeeping. Charges go through the
currency wallet (player -> house); purchase state is a CluePurchase row.
Network and Telegram work (fetching screenshots, sending clues) stays in
commands/shop/ — this module only decides and records."""

import enum
from dataclasses import dataclass
from datetime import UTC, datetime

from loguru import logger
from sqlalchemy import select
from sqlalchemy.orm import Session

from nani_pix_bot.models.clue_purchase import CluePurchase
from nani_pix_bot.models.currency_transfer import CurrencyTransfer
from nani_pix_bot.models.enums import ClueKind, CurrencyReason, GameStatus
from nani_pix_bot.models.game import Game
from nani_pix_bot.models.player import Player
from nani_pix_bot.services import game as game_service
from nani_pix_bot.services.clues import text
from nani_pix_bot.services.economy import config, wallet
from nani_pix_bot.services.economy.config import EconomyKey

MAX_EXTRA_SCREENSHOTS = 3
TILE_GRID = 8
TEXT_KINDS = frozenset({ClueKind.FIRST_LETTER, ClueKind.LAST_LETTER, ClueKind.TITLE_SHAPE})
_LETTER_KINDS = frozenset({ClueKind.FIRST_LETTER, ClueKind.LAST_LETTER})

_PRICE_KEYS = {
    ClueKind.LAST_LETTER: EconomyKey.CLUE_LAST_LETTER,
    ClueKind.FIRST_LETTER: EconomyKey.CLUE_FIRST_LETTER,
    ClueKind.TITLE_SHAPE: EconomyKey.CLUE_TITLE_SHAPE,
    ClueKind.TILE: EconomyKey.CLUE_TILE,
}
# Menu order: cheapest text clue first, image clues last.
_MENU_ORDER = (
    ClueKind.LAST_LETTER,
    ClueKind.FIRST_LETTER,
    ClueKind.TITLE_SHAPE,
    ClueKind.SCREENSHOT,
    ClueKind.TILE,
)


class Refusal(enum.StrEnum):
    NO_GAME = "no_game"  # no ACTIVE game, or a different one than the button's
    SETTER = "setter"
    ALREADY_OWNED = "already_owned"
    UNAVAILABLE = "unavailable"
    INSUFFICIENT = "insufficient"


class ShopRefusedError(Exception):
    def __init__(self, refusal: Refusal) -> None:
        super().__init__(refusal.value)
        self.refusal = refusal


@dataclass(frozen=True)
class Offer:
    kind: ClueKind
    price: int
    affordable: bool
    owned_screenshots: int = 0  # screenshots the buyer holds (SCREENSHOT offer only)


@dataclass(frozen=True)
class PurchaseRequest:
    kind: ClueKind
    tile_index: int | None = None
    screenshot_url: str | None = None


def _purchases(session: Session, game_id: int, player_id: int) -> list[CluePurchase]:
    stmt = select(CluePurchase).where(
        CluePurchase.game_id == game_id, CluePurchase.player_id == player_id
    )
    return list(session.scalars(stmt))


def owned_kinds(session: Session, game_id: int, player_id: int) -> set[ClueKind]:
    return {ClueKind(p.kind) for p in _purchases(session, game_id, player_id)}


def round_tile(session: Session, game_id: int) -> int | None:
    """The round's one tile: the position the game's first tile buyer
    picked, which every later buyer gets too. None until someone buys."""
    stmt = (
        select(CluePurchase.tile_index)
        .where(CluePurchase.game_id == game_id, CluePurchase.kind == ClueKind.TILE)
        .order_by(CluePurchase.id)
        .limit(1)
    )
    return session.scalars(stmt).first()


def owned_screenshot_urls(session: Session, game_id: int, player_id: int) -> set[str]:
    return {
        p.screenshot_url
        for p in _purchases(session, game_id, player_id)
        if p.kind == ClueKind.SCREENSHOT and p.screenshot_url is not None
    }


def active_game_for(session: Session, game_id: int) -> Game | None:
    """The ACTIVE game, iff its id is `game_id`."""
    game = game_service.active_or_setup_game(session)
    if game is None or game.status != GameStatus.ACTIVE or game.id != game_id:
        return None
    return game


def _screenshot_count(session: Session, game: Game, buyer: Player) -> int:
    return len(owned_screenshot_urls(session, game.id, buyer.telegram_user_id))


def price(session: Session, game: Game, buyer: Player, kind: ClueKind) -> int:
    amounts = config.get_amounts(session)
    if kind is ClueKind.SCREENSHOT:
        already = _screenshot_count(session, game, buyer)
        return (
            amounts[EconomyKey.CLUE_SCREENSHOT] + amounts[EconomyKey.CLUE_SCREENSHOT_STEP] * already
        )
    return amounts[_PRICE_KEYS[kind]]


def _has_screenshot_provider(game: Game) -> bool:
    return any(
        getattr(game, provider.id_attr_name) is not None
        for provider in game_service.SCREENSHOT_CAPABLE_PROVIDERS
    )


def _available(session: Session, game: Game, buyer: Player, kind: ClueKind, lang: str) -> bool:
    if kind in _LETTER_KINDS:
        return any(text.first_char(title) for _, title in game_service.clue_titles(game, lang))
    if kind in TEXT_KINDS:
        return game_service.display_title_field(game, lang) is not None
    if kind is ClueKind.SCREENSHOT:
        return (
            _has_screenshot_provider(game)
            and _screenshot_count(session, game, buyer) < MAX_EXTRA_SCREENSHOTS
        )
    # TILE: only a single-original-image game, and once per player.
    return not game.hard_mode and ClueKind.TILE not in owned_kinds(
        session, game.id, buyer.telegram_user_id
    )


def _already_owned(session: Session, game: Game, buyer: Player, request: PurchaseRequest) -> bool:
    player_id = buyer.telegram_user_id
    if request.kind in TEXT_KINDS or request.kind is ClueKind.TILE:
        return request.kind in owned_kinds(session, game.id, player_id)
    return False


def offers(session: Session, game: Game, buyer: Player, lang: str) -> list[Offer]:
    """What the shop menu lists: available, not-yet-owned items. Owned
    text clues and the tile drop out; the screenshot stays while more can
    be bought.
    `lang` picks the title fallback chain, so availability matches the
    title the clue will actually use."""
    owned = owned_kinds(session, game.id, buyer.telegram_user_id)
    result = []
    for kind in _MENU_ORDER:
        if kind in owned and kind in TEXT_KINDS:
            continue
        if not _available(session, game, buyer, kind, lang):
            continue
        cost = price(session, game, buyer, kind)
        held = _screenshot_count(session, game, buyer) if kind is ClueKind.SCREENSHOT else 0
        result.append(Offer(kind, cost, buyer.currency >= cost, held))
    return result


def _malformed(session: Session, game: Game, request: PurchaseRequest) -> bool:
    """A request missing (or out of range on) the detail its kind needs; a
    tile must also be the round's tile once someone has chosen it."""
    if request.kind is ClueKind.TILE:
        index = request.tile_index
        if not isinstance(index, int) or not 0 <= index < TILE_GRID * TILE_GRID:
            return True
        chosen = round_tile(session, game.id)
        return chosen is not None and index != chosen
    if request.kind is ClueKind.SCREENSHOT:
        return not request.screenshot_url
    return False


def _refusal(
    session: Session, game: Game, buyer: Player, request: PurchaseRequest
) -> Refusal | None:
    if game.status != GameStatus.ACTIVE:
        return Refusal.NO_GAME
    if buyer.telegram_user_id == game.starter_id:
        return Refusal.SETTER
    if _already_owned(session, game, buyer, request):
        return Refusal.ALREADY_OWNED
    # The purchase doesn't know the group language, so a clue is sellable
    # if it would be offered in any supported one.
    if _malformed(session, game, request) or not any(
        _available(session, game, buyer, request.kind, lang) for lang in ("en", "ru")
    ):
        return Refusal.UNAVAILABLE
    return None


def purchase(session: Session, game: Game, buyer: Player, request: PurchaseRequest) -> CluePurchase:
    refusal = _refusal(session, game, buyer, request)
    if refusal is not None:
        logger.warning(
            "Player {} refused {} in game {}: {}",
            buyer.telegram_user_id,
            request.kind,
            game.id,
            refusal,
        )
        raise ShopRefusedError(refusal)
    cost = price(session, game, buyer, request.kind)
    try:
        charge = wallet.debit(
            session,
            buyer,
            cost,
            wallet.LedgerEntry(CurrencyReason.CLUE_PURCHASE, game_id=game.id),
        )
    except wallet.InsufficientCurrencyError as error:
        logger.warning(
            "Player {} cannot afford {} ({}) in game {}",
            buyer.telegram_user_id,
            request.kind,
            cost,
            game.id,
        )
        raise ShopRefusedError(Refusal.INSUFFICIENT) from error
    session.flush()  # charge.id for the purchase's transfer_id
    bought = CluePurchase(
        game_id=game.id,
        player_id=buyer.telegram_user_id,
        kind=request.kind,
        tile_index=request.tile_index,
        screenshot_url=request.screenshot_url,
        transfer_id=charge.id,
    )
    session.add(bought)
    session.flush()
    logger.info(
        "Player {} bought {} for {} in game {}",
        buyer.telegram_user_id,
        request.kind,
        cost,
        game.id,
    )
    return bought


def refund(session: Session, purchase_row: CluePurchase) -> None:
    """Undo a purchase whose clue could not be delivered: pay the charge
    back (house -> player, naming it via reverses_id) and drop the row."""
    charge = session.get(CurrencyTransfer, purchase_row.transfer_id)
    buyer = session.get(Player, purchase_row.player_id)
    if charge is None or buyer is None:
        msg = f"refund of purchase {purchase_row.id}: missing charge or buyer"
        raise ValueError(msg)
    wallet.credit(
        session,
        buyer,
        charge.amount,
        wallet.LedgerEntry(
            CurrencyReason.REFUND, game_id=purchase_row.game_id, reverses_id=charge.id
        ),
    )
    session.delete(purchase_row)
    logger.info(
        "Refunded purchase {} ({} to player {})",
        purchase_row.id,
        charge.amount,
        buyer.telegram_user_id,
    )


def refund_game(session: Session, game_id: int) -> int:
    """Refund every clue purchase of a game that is being deleted (its
    purchase rows would otherwise vanish with it, unrefunded). Returns
    how many were refunded."""
    rows = list(session.scalars(select(CluePurchase).where(CluePurchase.game_id == game_id)))
    for row in rows:
        refund(session, row)
    logger.info("Refunded {} clue purchases of game {}", len(rows), game_id)
    return len(rows)


def mark_shared(purchase_row: CluePurchase) -> bool:
    """Stamp the clue as shared with the group; False if it already was."""
    if purchase_row.shared_at is not None:
        return False
    purchase_row.shared_at = datetime.now(UTC)
    return True
