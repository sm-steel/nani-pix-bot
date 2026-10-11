"""What a player's /profile card shows (#413): the season stamps (≤ STAMPS_MAX)
and achievements (≤ BADGES_MAX) they picked, in the order they picked them.
Without picks, or when none still resolves, the card shows the newest seasons
played and the rarest achievements held."""

import enum
from dataclasses import dataclass
from datetime import datetime

from loguru import logger
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from nani_pix_bot.models.achievement import AchievementGrant
from nani_pix_bot.models.cosmetics import ShowcasePick
from nani_pix_bot.models.enums import Rarity, SeasonStatus, ShowcaseKind
from nani_pix_bot.models.season import SeasonResult, SeasonSchedule
from nani_pix_bot.services.cards.profile_card import PROFILE_BADGES_MAX, PROFILE_STAMPS_MAX

STAMPS_MAX = PROFILE_STAMPS_MAX
BADGES_MAX = PROFILE_BADGES_MAX
LIMITS = {ShowcaseKind.STAMP: STAMPS_MAX, ShowcaseKind.ACHIEVEMENT: BADGES_MAX}
_RARITY_RANK = {rarity: rank for rank, rarity in enumerate(Rarity)}


class Toggle(enum.Enum):
    ADDED = "added"
    REMOVED = "removed"
    FULL = "full"  # the limit is reached
    UNKNOWN = "unknown"  # not something this player holds (a stale or forged button)


@dataclass(frozen=True)
class Held:
    """An achievement the player can show: the highest tier of one achievement
    (per period, for the champion ones)."""

    grant_id: int
    key: str
    tier: int
    period_key: str
    rarity: Rarity
    granted_at: datetime


def stamp_seasons(session: Session, player_id: int) -> list[tuple[int, str]]:
    """(season_schedule id, run id) of the ended seasons the player has a result
    in, newest first."""
    stmt = (
        select(SeasonSchedule.id, SeasonSchedule.run_id)
        .join(SeasonResult, SeasonResult.season_id == SeasonSchedule.id)
        .where(SeasonResult.player_id == player_id, SeasonSchedule.status == SeasonStatus.ENDED)
        .order_by(SeasonSchedule.end_at.desc(), SeasonSchedule.id.desc())
    )
    return [(season_id, run_id) for season_id, run_id in session.execute(stmt)]


def stamp_choices(session: Session, player_id: int) -> list[str]:
    """The run ids of the ended seasons the player has a result in, newest first."""
    return [run_id for _season_id, run_id in stamp_seasons(session, player_id)]


def _rarest_first(held: Held) -> tuple[int, int, float, int]:
    return (-_RARITY_RANK[held.rarity], -held.tier, -held.granted_at.timestamp(), -held.grant_id)


def badge_choices(session: Session, player_id: int) -> list[Held]:
    """The achievements the player holds, the highest tier of each (per period),
    rarest first: rarity, then tier, then the newest grant."""
    grants = session.scalars(
        select(AchievementGrant).where(AchievementGrant.player_id == player_id)
    )
    best: dict[tuple[str, str], AchievementGrant] = {}
    for grant in grants:
        slot = (grant.key, grant.period_key)
        if slot not in best or grant.tier > best[slot].tier:
            best[slot] = grant
    held = [Held(g.id, g.key, g.tier, g.period_key, g.rarity, g.granted_at) for g in best.values()]
    return sorted(held, key=_rarest_first)


def picks(session: Session, player_id: int, kind: ShowcaseKind) -> list[str]:
    """The player's picks of `kind`, in the order they were ticked."""
    stmt = (
        select(ShowcasePick.ref)
        .where(ShowcasePick.player_id == player_id, ShowcasePick.kind == kind)
        .order_by(ShowcasePick.position)
    )
    return list(session.scalars(stmt))


def _choice_refs(session: Session, player_id: int, kind: ShowcaseKind) -> list[str]:
    if kind is ShowcaseKind.STAMP:
        return stamp_choices(session, player_id)
    return [str(h.grant_id) for h in badge_choices(session, player_id)]


def toggle(session: Session, player_id: int, kind: ShowcaseKind, ref: str) -> Toggle:
    """Show or hide one stamp or achievement; a newly shown one goes last."""
    if ref not in _choice_refs(session, player_id, kind):
        logger.warning("refused to pin {kind} {ref!r}: not held", kind=kind.value, ref=ref)
        return Toggle.UNKNOWN
    existing = session.get(ShowcasePick, (player_id, kind, ref))
    if existing is not None:
        session.delete(existing)
        session.flush()
        logger.info("unpinned {kind} {ref!r} from their profile", kind=kind.value, ref=ref)
        return Toggle.REMOVED
    if len(picks(session, player_id, kind)) >= LIMITS[kind]:
        logger.warning(
            "refused to pin {kind} {ref!r}: already {limit} shown",
            kind=kind.value,
            ref=ref,
            limit=LIMITS[kind],
        )
        return Toggle.FULL
    last = session.scalar(
        select(func.max(ShowcasePick.position)).where(
            ShowcasePick.player_id == player_id, ShowcasePick.kind == kind
        )
    )
    session.add(ShowcasePick(player_id=player_id, kind=kind, ref=ref, position=(last or 0) + 1))
    session.flush()
    logger.info("pinned {kind} {ref!r} to their profile", kind=kind.value, ref=ref)
    return Toggle.ADDED


def shown_stamps(session: Session, player_id: int) -> list[str]:
    """The run ids the card shows: the resolving picks, else the newest seasons."""
    choices = stamp_choices(session, player_id)
    picked = [ref for ref in picks(session, player_id, ShowcaseKind.STAMP) if ref in choices]
    return picked or choices[:STAMPS_MAX]


def shown_badges(session: Session, player_id: int) -> list[Held]:
    """The achievements the card shows: the resolving picks, else the rarest."""
    choices = badge_choices(session, player_id)
    by_ref = {str(h.grant_id): h for h in choices}
    refs = picks(session, player_id, ShowcaseKind.ACHIEVEMENT)
    picked = [by_ref[ref] for ref in refs if ref in by_ref]
    return picked or choices[:BADGES_MAX]
