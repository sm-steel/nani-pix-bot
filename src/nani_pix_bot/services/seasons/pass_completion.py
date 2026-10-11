"""A pass track finishing (seasons spec §2): when the free track completes it is
stamped on the pass row and queued for its DM; either track emits
TRACK_COMPLETED so the achievements can react."""

from dataclasses import dataclass
from datetime import UTC, datetime

from loguru import logger
from sqlalchemy import select
from sqlalchemy.orm import Session

from nani_pix_bot.models.enums import EventType
from nani_pix_bot.models.season import SeasonPass, SeasonSchedule
from nani_pix_bot.seasons.definition import SeasonRun
from nani_pix_bot.services import events, players
from nani_pix_bot.services.achievements import outbox


@dataclass(frozen=True)
class Done:
    """Which of a player's two tracks are fully granted."""

    free: bool
    premium: bool


def done(progress: SeasonPass, run: SeasonRun) -> Done:
    total = len(run.tiers)
    bought = progress.premium_transfer_id is not None
    return Done(progress.free_claimed >= total, bought and progress.premium_claimed >= total)


def _first_free(session: Session, season_id: int) -> bool:
    stmt = (
        select(SeasonPass.player_id)
        .where(SeasonPass.season_id == season_id, SeasonPass.free_completed_at.is_not(None))
        .limit(1)
    )
    return session.scalars(stmt).first() is None


@dataclass(frozen=True)
class _Who:
    season: SeasonSchedule
    run: SeasonRun
    player_id: int


def _emit(session: Session, who: _Who, *, premium: bool, first: bool) -> None:
    events.emit(
        session,
        EventType.TRACK_COMPLETED,
        events.Involved(actor_id=who.player_id),
        season_id=who.season.id,
        premium=premium,
        first=first,
    )
    logger.info(
        "{player} completed the {pass_track} track of {run_id}",
        player=players.describe_player_id(session, who.player_id),
        player_id=who.player_id,
        pass_track="premium" if premium else "free",
        run_id=who.run.run_id,
        first=first,
        season_id=who.season.id,
    )


def announce(
    session: Session, season: SeasonSchedule, run: SeasonRun, progress: SeasonPass, before: Done
) -> None:
    """Announce the tracks that finished since `before` was taken."""
    now = done(progress, run)
    pid = progress.player_id
    who = _Who(season, run, pid)
    if now.free and not before.free:
        first = _first_free(session, season.id)
        progress.free_completed_at = datetime.now(UTC)
        outbox.enqueue_pass_done(session, season.id, pid)
        _emit(session, who, premium=False, first=first)
    if now.premium and not before.premium:
        _emit(session, who, premium=True, first=False)
