"""Which hex is on a player right now, and the marks the prank hexes draw.
Models and sqlalchemy only, so the render paths can import it cheaply."""

from datetime import UTC, datetime

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from nani_pix_bot.models.enums import SeasonStatus
from nani_pix_bot.models.halloween import Hex
from nani_pix_bot.models.season import SeasonSchedule
from nani_pix_bot.seasons.mechanics import HexKind

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


def is_faceless(session: Session, player_id: int, now: datetime) -> bool:
    """Whether Nopperabo hides this player's name, flair and title right now."""
    hex_ = active_hex_for(session, player_id, now)
    return hex_ is not None and hex_.kind == HexKind.NOPPERABO


def marked_name(
    session: Session, player_id: int, name: str, flair_emoji: str | None, now: datetime
) -> str:
    """The name as shown in the group: plain (with its flair) unless a hex is on it,
    then marked with a skull; Nopperabo replaces the whole name."""
    hex_ = active_hex_for(session, player_id, now)
    if hex_ is None:
        return name if flair_emoji is None else f"{name} {flair_emoji}"
    if hex_.kind == HexKind.NOPPERABO:
        return f"{FACELESS} {SKULL}"
    shown = name if flair_emoji is None else f"{name} {flair_emoji}"
    return f"{shown} {SKULL}"


def guess_reaction(
    session: Session, player_id: int, equipped: str | None, won: bool, now: datetime
) -> str | None:
    """The reaction for a guess: Chochin's prank on every outcome, else the
    equipped one on a win only."""
    hex_ = active_hex_for(session, player_id, now)
    if hex_ is not None and hex_.kind == HexKind.CHOCHIN:
        return CHOCHIN_REACTION
    return equipped if won else None
