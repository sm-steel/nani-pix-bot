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
from nani_pix_bot.models.enums import ClueKind, CurrencyReason, GameStatus, Provider
from nani_pix_bot.models.game import Game
from nani_pix_bot.models.player import Player
from nani_pix_bot.services import game as game_service
from nani_pix_bot.services import players
from nani_pix_bot.services.clues import text
from nani_pix_bot.services.economy import config, wallet
from nani_pix_bot.services.economy.config import EconomyKey
from nani_pix_bot.services.game import guesses

MAX_EXTRA_SCREENSHOTS = 3
TILE_GRID = 8
# Providers whose screenshot lists are real in-episode frames. Tenrai is
# deliberately absent: its /pictures endpoint returns promotional art.
CLUE_SCREENSHOT_PROVIDERS: tuple[Provider, ...] = (Provider.SHIKIMORI, Provider.TMDB)
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
    REVEALED = "revealed"  # a partial match already made the title's shape public


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
        base = (
            amounts[EconomyKey.CLUE_SCREENSHOT] + amounts[EconomyKey.CLUE_SCREENSHOT_STEP] * already
        )
    else:
        base = amounts[_PRICE_KEYS[kind]]
    return _discounted(game, base)


def _discounted(game: Game, base: int) -> int:
    """HARD MODE's clue sale (issue #254) — rounded down, never below 1 💠."""
    percent = game.hard_mode_clue_discount if game.hard_mode else 0
    if percent <= 0:
        return base
    return max(1, base * (100 - percent) // 100)


def clue_screenshot_providers(game: Game) -> list[Provider]:
    """Where the extra-screenshot clue may fetch from, best first: only the
    providers with genuine in-episode frames (CLUE_SCREENSHOT_PROVIDERS) the
    game has an id for, with the provider the round's own screenshot came
    from ahead of the rest. Tenrai is never used here — its pictures are
    promotional art (posters/key visuals) that can show the title."""
    providers = [
        provider
        for provider in CLUE_SCREENSHOT_PROVIDERS
        if getattr(game, provider.id_attr_name) is not None
    ]
    if game.screenshot_source in providers:
        own = Provider(game.screenshot_source)
        providers.remove(own)
        providers.insert(0, own)
    return providers


def _has_screenshot_provider(game: Game) -> bool:
    return bool(clue_screenshot_providers(game))


def _available(session: Session, game: Game, buyer: Player, kind: ClueKind, lang: str) -> bool:
    if kind in _LETTER_KINDS:
        return any(text.first_char(title) for _, title in game_service.clue_titles(game, lang))
    if kind is ClueKind.TITLE_SHAPE and guesses.has_partial_reveal(session, game.id):
        return False
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


def _not_sellable(
    session: Session, game: Game, buyer: Player, request: PurchaseRequest
) -> Refusal | None:
    """Why this clue can't be sold in this round, if it can't."""
    if request.kind is ClueKind.TITLE_SHAPE and guesses.has_partial_reveal(session, game.id):
        return Refusal.REVEALED
    # The purchase doesn't know the group language, so a clue is sellable
    # if it would be offered in any supported one.
    if _malformed(session, game, request) or not any(
        _available(session, game, buyer, request.kind, lang) for lang in ("en", "ru")
    ):
        return Refusal.UNAVAILABLE
    return None


def _refusal(
    session: Session, game: Game, buyer: Player, request: PurchaseRequest
) -> Refusal | None:
    if game.status != GameStatus.ACTIVE:
        return Refusal.NO_GAME
    if buyer.telegram_user_id == game.starter_id:
        return Refusal.SETTER
    if _already_owned(session, game, buyer, request):
        return Refusal.ALREADY_OWNED
    return _not_sellable(session, game, buyer, request)


def _detail(request: PurchaseRequest) -> dict[str, object]:
    """Log kwargs for a purchase line: the clue's kind, plus which
    tile/screenshot, both as a field and as `detail`, its text in the message."""
    fields: dict[str, object] = {"kind": request.kind.value, "detail": ""}
    if request.kind is ClueKind.TILE:
        fields.update(detail=f" (tile {request.tile_index})", tile=request.tile_index)
    elif request.kind is ClueKind.SCREENSHOT:
        fields.update(detail=f" ({request.screenshot_url})", screenshot_url=request.screenshot_url)
    return fields


def purchase(session: Session, game: Game, buyer: Player, request: PurchaseRequest) -> CluePurchase:
    refusal = _refusal(session, game, buyer, request)
    if refusal is not None:
        logger.warning(
            "refused the {kind} clue{detail}: {reason}",
            reason=refusal.value,
            game_id=game.id,
            **_detail(request),
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
            "cannot afford the {kind} clue{detail} ({price} 💠)",
            price=cost,
            game_id=game.id,
            **_detail(request),
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
        "bought the {kind} clue{detail} for {price} 💠 (purchase {purchase_id})",
        price=cost,
        purchase_id=bought.id,
        game_id=game.id,
        **_detail(request),
    )
    return bought


@dataclass(frozen=True)
class RefundableItem:
    """One un-refunded clue purchase, as the admin /refund picker lists it."""

    purchase_id: int
    game_id: int
    kind: ClueKind
    amount: int
    created_at: datetime


def refundable_purchases(session: Session, player_id: int) -> list[RefundableItem]:
    """Every clue purchase `player_id` still holds, newest first. A refunded
    purchase's row is deleted (refund()), so whatever is here is refundable."""
    stmt = (
        select(CluePurchase, CurrencyTransfer.amount)
        .join(CurrencyTransfer, CurrencyTransfer.id == CluePurchase.transfer_id)
        .where(CluePurchase.player_id == player_id)
        .order_by(CluePurchase.created_at.desc(), CluePurchase.id.desc())
    )
    return [
        RefundableItem(row.id, row.game_id, ClueKind(row.kind), amount, row.created_at)
        for row, amount in session.execute(stmt)
    ]


def refund(session: Session, purchase_row: CluePurchase) -> int:
    """Undo a purchase — a clue that could not be delivered, or an admin
    /refund: pay the charge back (house -> player, naming it via
    reverses_id) and drop the row. Returns the refunded amount."""
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
        "refunded {kind} clue purchase {purchase_id} ({amount} 💠 to {recipient})",
        kind=ClueKind(purchase_row.kind).value,
        purchase_id=purchase_row.id,
        amount=charge.amount,
        recipient=players.describe_player_id(session, buyer.telegram_user_id),
        recipient_id=buyer.telegram_user_id,
        game_id=purchase_row.game_id,
    )
    return charge.amount


def refund_game(session: Session, game_id: int) -> int:
    """Refund every clue purchase of a game that is being deleted (its
    purchase rows would otherwise vanish with it, unrefunded). Returns
    how many were refunded."""
    rows = list(session.scalars(select(CluePurchase).where(CluePurchase.game_id == game_id)))
    for row in rows:
        refund(session, row)
    logger.info("refunded {count} clue purchase(s)", count=len(rows), game_id=game_id)
    return len(rows)


def mark_shared(purchase_row: CluePurchase) -> bool:
    """Stamp the clue as shared with the group; False if it already was."""
    if purchase_row.shared_at is not None:
        return False
    purchase_row.shared_at = datetime.now(UTC)
    return True
