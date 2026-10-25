"""Which hex is on a player right now, and the marks the prank hexes draw.
Models and sqlalchemy only, so the render paths can import it cheaply."""

from datetime import UTC, datetime

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from nani_pix_bot.models.enums import SeasonStatus
from nani_pix_bot.models.halloween import Hex
from nani_pix_bot.models.season import SeasonSchedule

# A closing season still runs its tagged games, so its hexes stay on.
LIVE_STATUSES = (SeasonStatus.ACTIVE, SeasonStatus.CLOSING)

SKULL = "💀"
FACELESS = "👤"
CHOCHIN_REACTION = "🤪"


def naive_utc(at: datetime) -> datetime:
    """UTC without tzinfo, how the DATETIME column stores it."""
    if at.tzinfo is None:
        return at
    return at.astimezone(UTC).replace(tzinfo=None)


def active_hex_for(session: Session, player_id: int, now: datetime) -> Hex | None:
    """The unresolved, unexpired hex on a player in an ACTIVE or CLOSING season."""
    stamp = naive_utc(now)
    stmt = (
        select(Hex)
        .join(SeasonSchedule, SeasonSchedule.id == Hex.season_id)
        .where(
            Hex.target_id == player_id,
            Hex.resolved_at.is_(None),
            SeasonSchedule.status.in_(LIVE_STATUSES),
            or_(Hex.expires_at.is_(None), Hex.expires_at > stamp),
        )
        .order_by(Hex.id.desc())
        .limit(1)
    )
    return session.scalars(stmt).first()
