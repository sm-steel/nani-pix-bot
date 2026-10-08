"""The one pre-rendered reveal video (issue #295): at most one row, ever.

`slot` is always 1, so a second row can't exist; reserving a new game
overwrites it. `game_id` is a plain column, not a FK, because /stop deletes
the game row before its reveal and the slot is only ever matched by value.
The bytes are part 1 of the reveal (the effect) as MPEG-TS, ready to be
joined with the win-time ending — see services/reveal/pipeline.py."""

from datetime import datetime

from sqlalchemy import LargeBinary, String
from sqlalchemy.orm import Mapped, mapped_column

from nani_pix_bot.models.base import Base
from nani_pix_bot.models.enums import RevealEffect, RevealStatus
from nani_pix_bot.models.game import IMAGE_COLUMN_LENGTH


class RevealVideo(Base):
    __tablename__ = "reveal_video"

    slot: Mapped[int] = mapped_column(primary_key=True, autoincrement=False, default=1)
    game_id: Mapped[int]
    effect: Mapped[RevealEffect]
    image_choice: Mapped[str | None] = mapped_column(String(1), default=None)
    status: Mapped[RevealStatus]
    part1_ts: Mapped[bytes | None] = mapped_column(
        LargeBinary(length=IMAGE_COLUMN_LENGTH), deferred=True, default=None
    )
    join_offset: Mapped[float | None] = mapped_column(default=None)
    created_at: Mapped[datetime]
    ready_at: Mapped[datetime | None] = mapped_column(default=None)
