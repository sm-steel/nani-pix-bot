import pytest
from sqlalchemy import func, select

from nani_pix_bot.models.cosmetics import OwnedItem
from nani_pix_bot.models.currency_transfer import CurrencyTransfer
from nani_pix_bot.models.enums import (
    ConsumableKind,
    Currency,
    CurrencyReason,
    ItemSource,
    SeasonStatus,
)
from nani_pix_bot.models.player import Player
from nani_pix_bot.models.season import SeasonShopPurchase
from nani_pix_bot.seasons import registry
from nani_pix_bot.services.cosmetics import consumables, inventory
from nani_pix_bot.services.cosmetics.inventory import ItemGrant
from nani_pix_bot.services.economy import wallet
from nani_pix_bot.services.economy.wallet import LedgerEntry
from nani_pix_bot.services.seasons import season_shop
from nani_pix_bot.services.seasons.season_shop import ShopRefusal, ShopRefusedError

DANGO, BORDER, TOKEN = 0, 1, 2


@pytest.fixture
def buyer(session, active_season) -> Player:
    player = Player(telegram_user_id=1, username="me")
    session.add(player)
    session.flush()
    entry = LedgerEntry(CurrencyReason.SEASON_WIN, season_id=active_season.id, coins=True)
    wallet.credit(session, player, 200, entry)
    return player


@pytest.fixture
def run():
    return registry.get("demo_1")


def _buy(session, season, run, buyer, index):
    return season_shop.buy(session, season, run, buyer, index)


def test_stock_lists_every_offer(session, active_season, run, buyer) -> None:
    rows = season_shop.stock(session, active_season, run, buyer.telegram_user_id)
    assert [r.offer.key for r in rows] == ["name_flair.dango", "host_frame.border", "clue_token"]
    assert [r.left for r in rows] == [1, 1, 2]
    assert rows[DANGO].item_id == "demo_1/name_flair.dango"
    assert rows[TOKEN].item_id is None


def test_buying_an_item_charges_coins_and_grants_it(session, active_season, run, buyer) -> None:
    after = _buy(session, active_season, run, buyer, DANGO)
    assert after.owned
    assert after.left == 0
    assert wallet.balance(session, buyer.telegram_user_id, season_id=active_season.id) == 160
    owned = session.scalars(select(OwnedItem)).one()
    assert (owned.item_id, owned.source, owned.season_id) == (
        "demo_1/name_flair.dango",
        ItemSource.SHOP,
        active_season.id,
    )
    purchase = session.scalars(select(SeasonShopPurchase)).one()
    charge = session.get(CurrencyTransfer, purchase.transfer_id)
    assert charge.currency == Currency.SEASON
    assert charge.reason == CurrencyReason.SEASON_SHOP
    assert charge.season_id == active_season.id


def test_buying_an_item_twice_charges_once(session, active_season, run, buyer) -> None:
    _buy(session, active_season, run, buyer, DANGO)
    with pytest.raises(ShopRefusedError) as refused:
        _buy(session, active_season, run, buyer, DANGO)
    assert refused.value.refusal is ShopRefusal.OWNED
    assert session.scalar(select(func.count()).select_from(SeasonShopPurchase)) == 1


def test_an_item_already_owned_from_the_pass_is_refused(session, active_season, run, buyer) -> None:
    inventory.grant(
        session,
        buyer.telegram_user_id,
        ItemGrant("demo_1/host_frame.border", ItemSource.TIER, active_season.id),
    )
    with pytest.raises(ShopRefusedError) as refused:
        _buy(session, active_season, run, buyer, BORDER)
    assert refused.value.refusal is ShopRefusal.OWNED


def test_the_clue_token_limit_holds(session, active_season, run, buyer) -> None:
    _buy(session, active_season, run, buyer, TOKEN)
    after = _buy(session, active_season, run, buyer, TOKEN)
    assert after.left == 0
    assert consumables.count(session, buyer.telegram_user_id, ConsumableKind.CLUE_TICKET) == 2
    with pytest.raises(ShopRefusedError) as refused:
        _buy(session, active_season, run, buyer, TOKEN)
    assert refused.value.refusal is ShopRefusal.SOLD_OUT
    assert wallet.balance(session, buyer.telegram_user_id, season_id=active_season.id) == 140


def test_not_enough_coins_is_refused_without_a_charge(session, active_season, run) -> None:
    poor = Player(telegram_user_id=3, username="poor")
    session.add(poor)
    session.flush()
    with pytest.raises(ShopRefusedError) as refused:
        _buy(session, active_season, run, poor, BORDER)
    assert refused.value.refusal is ShopRefusal.INSUFFICIENT
    assert session.scalar(select(func.count()).select_from(SeasonShopPurchase)) == 0
    assert session.scalar(select(func.count()).select_from(OwnedItem)) == 0


def test_the_shop_is_closed_once_the_season_is_closing(session, active_season, run, buyer) -> None:
    active_season.status = SeasonStatus.CLOSING
    with pytest.raises(ShopRefusedError) as refused:
        _buy(session, active_season, run, buyer, DANGO)
    assert refused.value.refusal is ShopRefusal.CLOSED


def test_an_index_outside_the_shop_is_unknown(session, active_season, run, buyer) -> None:
    with pytest.raises(ShopRefusedError) as refused:
        _buy(session, active_season, run, buyer, 99)
    assert refused.value.refusal is ShopRefusal.UNKNOWN


def test_a_purchase_is_logged(session, active_season, run, buyer, log_records) -> None:
    _buy(session, active_season, run, buyer, DANGO)
    record = next(r for r in log_records if "bought" in r.message)
    assert record.level == "INFO"
    assert record.extra["offer"] == "name_flair.dango"
    assert record.extra["season_id"] == active_season.id


def test_buying_ofuda_grants_the_consumable(session, active_season, buyer) -> None:
    run = registry.discover("tests.seasons.fake_runs")["demo_3"]
    index = next(i for i, o in enumerate(run.shop) if o.key == "ofuda")
    _buy(session, active_season, run, buyer, index)
    assert consumables.count(session, buyer.telegram_user_id, ConsumableKind.OFUDA) == 1
    assert consumables.count(session, buyer.telegram_user_id, ConsumableKind.CLUE_TICKET) == 0
    assert wallet.balance(session, buyer.telegram_user_id, season_id=active_season.id) == 160
