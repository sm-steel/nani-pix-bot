"""A player's profile (seasons spec §5): season stamps, equipped banner and title,
and the counts the card and caption show."""

from dataclasses import dataclass

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from nani_pix_bot.models.cosmetics import OwnedItem
from nani_pix_bot.models.enums import CosmeticSlot, SeasonStatus
from nani_pix_bot.models.player import Player
from nani_pix_bot.models.season import SeasonResult, SeasonSchedule
from nani_pix_bot.seasons import registry
from nani_pix_bot.services import players
from nani_pix_bot.services.achievements import titles
from nani_pix_bot.services.cards import season_background
from nani_pix_bot.services.cosmetics import inventory, looks


@dataclass(frozen=True)
class Stamp:
    run_id: str
    label: str
    gold: bool  # pass completed (Phase 3); never set yet


@dataclass(frozen=True)
class ProfileView:
    player_id: int
    name: str  # plain: the card can't draw emoji, so no flair here
    title: str | None
    banner: bytes | None
    stamps: tuple[Stamp, ...]  # newest season first
    seasons_played: int
    cosmetics_owned: int


def _played_run_ids(session: Session, player_id: int) -> list[str]:
    """The run ids of the ended seasons the player has a result in, newest first."""
    stmt = (
        select(SeasonSchedule.run_id)
        .join(SeasonResult, SeasonResult.season_id == SeasonSchedule.id)
        .where(SeasonResult.player_id == player_id, SeasonSchedule.status == SeasonStatus.ENDED)
        .order_by(SeasonSchedule.end_at.desc(), SeasonSchedule.id.desc())
    )
    return list(session.scalars(stmt))


def _stamp(run_id: str, lang: str) -> Stamp:
    run = registry.get(run_id)
    return Stamp(run_id, run.name(lang) if run else run_id, gold=False)


def _banner(session: Session, player_id: int, stamps: tuple[Stamp, ...]) -> bytes | None:
    equipped = inventory.equipped(session, player_id, CosmeticSlot.PROFILE_BANNER)
    if equipped is not None:
        return looks.banner_path(equipped.run.run_id, equipped.item.look).read_bytes()
    return season_background(stamps[0].run_id) if stamps else None


def build(session: Session, player_id: int, lang: str) -> ProfileView:
    stamps = tuple(_stamp(run_id, lang) for run_id in _played_run_ids(session, player_id))
    player = session.get(Player, player_id)
    owned = session.scalar(
        select(func.count()).select_from(OwnedItem).where(OwnedItem.player_id == player_id)
    )
    return ProfileView(
        player_id=player_id,
        name=players.display_name(session, player_id),
        title=titles.text(player.title_key if player else None, lang),
        banner=_banner(session, player_id, stamps),
        stamps=stamps,
        seasons_played=len(stamps),
        cosmetics_owned=owned or 0,
    )
