"""The Season Shop (seasons spec §4): what a player can still buy this season,
and buying it with the season's 🪙. Open only while the season is active."""

from dataclasses import dataclass
from enum import StrEnum
from typing import NoReturn

from loguru import logger
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from nani_pix_bot.models.enums import ConsumableKind, CurrencyReason, ItemSource, SeasonStatus
from nani_pix_bot.models.player import Player
from nani_pix_bot.models.season import SeasonSchedule, SeasonShopPurchase
from nani_pix_bot.seasons.definition import RewardKind, SeasonRun, ShopOffer
from nani_pix_bot.services.cosmetics import consumables, inventory
from nani_pix_bot.services.cosmetics.inventory import ItemGrant
from nani_pix_bot.services.economy import wallet
from nani_pix_bot.services.economy.wallet import InsufficientCurrencyError, LedgerEntry


class ShopRefusal(StrEnum):
    CLOSED = "closed"
    UNKNOWN = "unknown"
    OWNED = "owned"
    SOLD_OUT = "sold_out"
    INSUFFICIENT = "insufficient"


class ShopRefusedError(Exception):
    def __init__(self, refusal: ShopRefusal) -> None:
        super().__init__(refusal.value)
        self.refusal = refusal


@dataclass(frozen=True)
class Stock:
    offer: ShopOffer
    item_id: str | None
    bought: int
    owned: bool

    @property
    def left(self) -> int:
        if self.owned:
            return 0
        return max(0, self.offer.limit - self.bought)


def _item_id(run: SeasonRun, offer: ShopOffer) -> str | None:
    return f"{run.run_id}/{offer.item_key}" if offer.kind is RewardKind.ITEM else None


def _bought(session: Session, season_id: int, player_id: int, key: str) -> int:
    stmt = (
        select(func.count())
        .select_from(SeasonShopPurchase)
        .where(
            SeasonShopPurchase.season_id == season_id,
            SeasonShopPurchase.player_id == player_id,
            SeasonShopPurchase.offer_key == key,
        )
    )
    return session.scalar(stmt) or 0


def _stock_of(
    session: Session, season: SeasonSchedule, run: SeasonRun, player_id: int, offer: ShopOffer
) -> Stock:
    item_id = _item_id(run, offer)
    owned = item_id is not None and inventory.owns(session, player_id, item_id)
    return Stock(offer, item_id, _bought(session, season.id, player_id, offer.key), owned)


def stock(session: Session, season: SeasonSchedule, run: SeasonRun, player_id: int) -> list[Stock]:
    return [_stock_of(session, season, run, player_id, offer) for offer in run.shop]


def _refuse(refusal: ShopRefusal, season: SeasonSchedule, run: SeasonRun, **fields) -> NoReturn:
    logger.warning(
        "season shop purchase refused: {refusal}",
        refusal=refusal.value,
        season_id=season.id,
        run_id=run.run_id,
        **fields,
    )
    raise ShopRefusedError(refusal)


def _check_buyable(
    session: Session, season: SeasonSchedule, run: SeasonRun, player_id: int, index: int
) -> Stock:
    if season.status != SeasonStatus.ACTIVE:
        _refuse(ShopRefusal.CLOSED, season, run, index=index)
    if not 0 <= index < len(run.shop):
        _refuse(ShopRefusal.UNKNOWN, season, run, index=index)
    before = _stock_of(session, season, run, player_id, run.shop[index])
    if before.owned:
        _refuse(ShopRefusal.OWNED, season, run, index=index, offer=before.offer.key)
    if before.left == 0:
        _refuse(ShopRefusal.SOLD_OUT, season, run, index=index, offer=before.offer.key)
    return before


def _deliver(
    session: Session, season: SeasonSchedule, run: SeasonRun, pid: int, row: Stock
) -> None:
    if row.item_id is not None:
        inventory.grant(session, pid, ItemGrant(row.item_id, ItemSource.SHOP, season.id))
    else:
        kind = ConsumableKind(row.offer.kind.value)
        consumables.add(session, pid, kind, why=f"{run.run_id} shop")


def buy(
    session: Session, season: SeasonSchedule, run: SeasonRun, player: Player, index: int
) -> Stock:
    pid = player.telegram_user_id
    before = _check_buyable(session, season, run, pid, index)
    offer = before.offer
    entry = LedgerEntry(CurrencyReason.SEASON_SHOP, season_id=season.id, coins=True)
    try:
        charge = wallet.debit(session, player, offer.price, entry)
    except InsufficientCurrencyError:
        _refuse(
            ShopRefusal.INSUFFICIENT, season, run, index=index, offer=offer.key, price=offer.price
        )
    _deliver(session, season, run, pid, before)
    session.add(
        SeasonShopPurchase(
            season_id=season.id, player_id=pid, offer_key=offer.key, transfer_id=charge.id
        )
    )
    session.flush()
    logger.info(
        "bought {offer} in the season shop for {price} 🪙",
        offer=offer.key,
        price=offer.price,
        season_id=season.id,
        run_id=run.run_id,
        transfer_id=charge.id,
    )
    return _stock_of(session, season, run, pid, offer)
