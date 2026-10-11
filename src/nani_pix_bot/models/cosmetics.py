from datetime import UTC, datetime

from sqlalchemy import BigInteger, ForeignKey, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from nani_pix_bot.models.base import Base
from nani_pix_bot.models.enums import CosmeticSlot, ItemSource, PreviewKind

ITEM_ID_LENGTH = 96
SLOT_LENGTH = 24
SOURCE_LENGTH = 16
PREVIEW_KIND_LENGTH = 8
FILE_ID_LENGTH = 256
PREVIEW_LANG_LENGTH = 8


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
