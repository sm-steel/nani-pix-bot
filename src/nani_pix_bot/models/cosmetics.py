from datetime import UTC, datetime

from sqlalchemy import BigInteger, CheckConstraint, ForeignKey, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from nani_pix_bot.models.base import Base
from nani_pix_bot.models.enums import (
    ConsumableKind,
    CosmeticSlot,
    ItemSource,
    PreviewKind,
    ShowcaseKind,
)

ITEM_ID_LENGTH = 96
SHOWCASE_KIND_LENGTH = 16
SHOWCASE_REF_LENGTH = 64
SLOT_LENGTH = 24
SOURCE_LENGTH = 16
PREVIEW_KIND_LENGTH = 8
FILE_ID_LENGTH = 256
PREVIEW_LANG_LENGTH = 8
CONSUMABLE_KIND_LENGTH = 16


class OwnedItem(Base):
    """A season cosmetic a player owns, forever (seasons spec §3). `item_id`
    is `<run_id>/<slot>.<name>` from a frozen run module, so it always resolves."""

    __tablename__ = "owned_items"
    __table_args__ = (UniqueConstraint("player_id", "item_id", name="uq_owned_item"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    player_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("players.telegram_user_id"), index=True
    )
    item_id: Mapped[str] = mapped_column(String(ITEM_ID_LENGTH))
    source: Mapped[ItemSource] = mapped_column(String(SOURCE_LENGTH))
    season_id: Mapped[int | None] = mapped_column(ForeignKey("season_schedule.id"), default=None)
    acquired_at: Mapped[datetime] = mapped_column(default=lambda: datetime.now(UTC))


class EquippedItem(Base):
    """The one item a player has equipped in a slot (seasons spec §3)."""

    __tablename__ = "equipped_items"

    player_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("players.telegram_user_id"), primary_key=True
    )
    slot: Mapped[CosmeticSlot] = mapped_column(String(SLOT_LENGTH), primary_key=True)
    item_id: Mapped[str] = mapped_column(String(ITEM_ID_LENGTH))


class ItemPreview(Base):
    """The Telegram file_id of an item's rendered preview (seasons spec §3), so
    each preview is rendered and uploaded once per language (banners and win effects draw
    text). Text previews aren't stored."""

    __tablename__ = "item_previews"

    item_id: Mapped[str] = mapped_column(String(ITEM_ID_LENGTH), primary_key=True)
    lang: Mapped[str] = mapped_column(String(PREVIEW_LANG_LENGTH), primary_key=True)
    kind: Mapped[PreviewKind] = mapped_column(String(PREVIEW_KIND_LENGTH))
    file_id: Mapped[str] = mapped_column(String(FILE_ID_LENGTH))
    created_at: Mapped[datetime] = mapped_column(default=lambda: datetime.now(UTC))


class ShowcasePick(Base):
    """One thing a player chose to show on their /profile card (#413), in the
    order they picked it. A pick that no longer resolves is skipped on render."""

    __tablename__ = "profile_showcase"

    player_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("players.telegram_user_id"), primary_key=True
    )
    kind: Mapped[ShowcaseKind] = mapped_column(String(SHOWCASE_KIND_LENGTH), primary_key=True)
    ref: Mapped[str] = mapped_column(String(SHOWCASE_REF_LENGTH), primary_key=True)
    position: Mapped[int]


class Consumable(Base):
    """How many of a consumable a player holds (seasons spec §3)."""

    __tablename__ = "consumables"
    __table_args__ = (CheckConstraint("count >= 0", name="ck_consumables_count_non_negative"),)

    player_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("players.telegram_user_id"), primary_key=True
    )
    kind: Mapped[ConsumableKind] = mapped_column(String(CONSUMABLE_KIND_LENGTH), primary_key=True)
    count: Mapped[int] = mapped_column(default=0)
