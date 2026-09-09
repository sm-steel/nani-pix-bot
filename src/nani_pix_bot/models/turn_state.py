from sqlalchemy import BigInteger, ForeignKey
from sqlalchemy.orm import Mapped, mapped_column

from nani_pix_bot.models.base import Base


class TurnState(Base):
    """Single row (id=1) tracking who may start the next game.

    `next_starter_id` of None means the turn is open to anyone — see
    MECHANICS.md's "Turn handoff" section.
    """

    __tablename__ = "turn_state"

    id: Mapped[int] = mapped_column(primary_key=True)
    next_starter_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("players.telegram_user_id"), default=None
    )
