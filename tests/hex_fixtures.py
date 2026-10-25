"""A hex row on a player, for tests outside services/seasons that only need its marks."""

from datetime import UTC, datetime, timedelta

from sqlalchemy.orm import Session

from nani_pix_bot.models.enums import SeasonStatus
from nani_pix_bot.models.halloween import Hex
from nani_pix_bot.models.season import SeasonSchedule
from nani_pix_bot.seasons.mechanics import HexKind


def add_hex(session: Session, target_id: int, kind: HexKind, *, hours: int | None = None) -> Hex:
    """An unresolved hex on `target_id` in an ACTIVE season (a new one each call).
    Pranks get `hours` of life from the real clock."""
    now = datetime.now(UTC).replace(tzinfo=None)
    season = SeasonSchedule(
        run_id="demo_3",
        start_at=now - timedelta(days=1),
        end_at=now + timedelta(days=9),
        status=SeasonStatus.ACTIVE,
        created_by=9,
    )
    session.add(season)
    session.flush()
    row = Hex(
        season_id=season.id,
        kind=kind.value,
        caster_id=target_id,
        target_id=target_id,
        cast_at=now,
        expires_at=None if hours is None else now + timedelta(hours=hours),
    )
    session.add(row)
    session.flush()
    return row
