from datetime import UTC, datetime

import pytest
from sqlalchemy import func, select

from nani_pix_bot.models.cosmetics import EquippedItem, OwnedItem
from nani_pix_bot.models.enums import CosmeticSlot, ItemSource
from nani_pix_bot.models.player import Player
from nani_pix_bot.services.cosmetics import inventory
from nani_pix_bot.services.cosmetics.inventory import ItemGrant

ALICE = 2
FLAIR = "demo_1/name_flair.blossom"


@pytest.fixture
def alice(session):
    session.add(Player(telegram_user_id=ALICE, username="alice"))
    session.flush()
    return ALICE


def test_grant_then_equip(session, alice) -> None:
    assert inventory.grant(session, alice, ItemGrant(FLAIR, ItemSource.ADMIN))
    assert inventory.owns(session, alice, FLAIR)
    inventory.equip(session, alice, FLAIR)
    assert inventory.look(session, alice, CosmeticSlot.NAME_FLAIR) == "🌸"


def test_grant_twice_keeps_one_row(session, alice) -> None:
    assert inventory.grant(session, alice, ItemGrant(FLAIR, ItemSource.ADMIN))
    assert not inventory.grant(session, alice, ItemGrant(FLAIR, ItemSource.ADMIN))
    assert session.scalar(select(func.count(OwnedItem.id))) == 1


def test_grant_unknown_item_is_a_bug(session, alice) -> None:
    with pytest.raises(ValueError, match="unknown item"):
        inventory.grant(session, alice, ItemGrant("demo_1/nope", ItemSource.ADMIN))


def test_cannot_equip_unowned(session, alice) -> None:
    with pytest.raises(inventory.EquipRefusedError) as refused:
        inventory.equip(session, alice, FLAIR)
    assert refused.value.reason == "not_owned"
    assert session.scalar(select(func.count()).select_from(EquippedItem)) == 0


def test_one_item_per_slot_and_unequip(session, alice) -> None:
    inventory.grant(session, alice, ItemGrant(FLAIR, ItemSource.ADMIN))
    inventory.equip(session, alice, FLAIR)
    inventory.equip(session, alice, FLAIR)  # idempotent
    assert session.scalar(select(func.count()).select_from(EquippedItem)) == 1
    inventory.unequip(session, alice, CosmeticSlot.NAME_FLAIR)
    assert inventory.equipped(session, alice, CosmeticSlot.NAME_FLAIR) is None


def test_owned_filters_by_slot(session, alice) -> None:
    inventory.grant(session, alice, ItemGrant(FLAIR, ItemSource.ADMIN))
    inventory.grant(session, alice, ItemGrant("demo_1/title.demo", ItemSource.ADMIN))
    assert [r.item_id for r in inventory.owned(session, alice, CosmeticSlot.TITLE)] == [
        "demo_1/title.demo"
    ]
    assert len(inventory.owned(session, alice)) == 2


def test_equipped_with_unresolvable_item_is_none(session, alice, log_records) -> None:
    session.add(
        EquippedItem(player_id=alice, slot=CosmeticSlot.NAME_FLAIR, item_id="gone_1/name_flair.x")
    )
    session.flush()
    assert inventory.look(session, alice, CosmeticSlot.NAME_FLAIR) is None
    assert any(r.level == "WARNING" for r in log_records)


def test_grant_is_logged(session, alice, log_records) -> None:
    inventory.grant(session, alice, ItemGrant(FLAIR, ItemSource.ADMIN))
    line = next(r for r in log_records if r.message.startswith("granted"))
    assert line.extra["item_id"] == FLAIR


def test_owned_comes_back_in_acquisition_order(session, alice) -> None:
    inventory.grant(session, alice, ItemGrant("demo_1/title.demo", ItemSource.ADMIN))
    inventory.grant(session, alice, ItemGrant(FLAIR, ItemSource.ADMIN))
    owned_ids = [r.item_id for r in inventory.owned(session, alice)]
    assert owned_ids == ["demo_1/title.demo", FLAIR]
    older = session.scalars(select(OwnedItem).where(OwnedItem.item_id == FLAIR)).one()
    older.acquired_at = datetime(2000, 1, 1, tzinfo=UTC)
    session.flush()
    assert [r.item_id for r in inventory.owned(session, alice)] == [FLAIR, "demo_1/title.demo"]
