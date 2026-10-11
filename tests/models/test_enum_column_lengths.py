"""A stored enum value longer than its String(N) column breaks on MariaDB only."""

import pytest

from nani_pix_bot.models.announcement import KIND_LENGTH as OUTBOX_KIND_LENGTH
from nani_pix_bot.models.cosmetics import CONSUMABLE_KIND_LENGTH, SOURCE_LENGTH
from nani_pix_bot.models.currency_transfer import REASON_LENGTH
from nani_pix_bot.models.enums import (
    ConsumableKind,
    CurrencyReason,
    EventType,
    ItemSource,
    OutboxKind,
    XpSource,
)
from nani_pix_bot.models.event_log import EVENT_TYPE_LENGTH
from nani_pix_bot.models.season import XP_SOURCE_LENGTH


@pytest.mark.parametrize(
    ("enum", "length"),
    [
        (OutboxKind, OUTBOX_KIND_LENGTH),
        (ItemSource, SOURCE_LENGTH),
        (CurrencyReason, REASON_LENGTH),
        (ConsumableKind, CONSUMABLE_KIND_LENGTH),
        (XpSource, XP_SOURCE_LENGTH),
        (EventType, EVENT_TYPE_LENGTH),
    ],
)
def test_every_stored_value_fits_its_column(enum, length: int) -> None:
    assert max(len(member.value) for member in enum) <= length
