import pytest

from nani_pix_bot.models.cosmetics import EquippedItem
from nani_pix_bot.models.enums import CosmeticSlot, ItemSource
from nani_pix_bot.models.player import Player
from nani_pix_bot.services.cosmetics import flair, inventory
from nani_pix_bot.services.cosmetics.inventory import ItemGrant

ALICE = 2
FLAIR = "demo_1/name_flair.blossom"


@pytest.fixture
def alice(session):
    session.add(Player(telegram_user_id=ALICE, username="alice"))
    session.flush()
    return ALICE


def test_bare_name_with_nothing_equipped(session, alice) -> None:
    assert flair.named(session, alice, "@alice") == "@alice"


def test_flair_is_appended_once_equipped(session, alice) -> None:
    inventory.grant(session, alice, ItemGrant(FLAIR, ItemSource.ADMIN))
    inventory.equip(session, alice, FLAIR)
    assert flair.named(session, alice, "@alice") == "@alice 🌸"


def test_unresolvable_equipped_item_gives_bare_name(session, alice) -> None:
    session.add(
        EquippedItem(player_id=alice, slot=CosmeticSlot.NAME_FLAIR, item_id="gone_1/name_flair.x")
    )
    session.flush()
    assert flair.named(session, alice, "@alice") == "@alice"
