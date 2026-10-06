from sqlalchemy import BigInteger, ForeignKey, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from nani_pix_bot.models.base import Base


class GameVote(Base):
    """One player's current vote in a hard-mode game's vote (issue #252).
    Votes can be changed, so there is one row per (game, voter)."""

    __tablename__ = "game_votes"
    __table_args__ = (UniqueConstraint("game_id", "voter_id", name="uq_game_votes_game_voter"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    game_id: Mapped[int] = mapped_column(ForeignKey("games.id", ondelete="CASCADE"), index=True)
    voter_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("players.telegram_user_id"))
    candidate_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("players.telegram_user_id"))
