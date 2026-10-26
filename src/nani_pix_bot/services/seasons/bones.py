"""The Bone Collector set (Halloween spec): every win of a haunted or boss
game of the season is a bone, and the set's item is granted at the run's bone
count. Must not import services.game."""

from loguru import logger
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from nani_pix_bot.models.announcement import AnnouncementOutbox
from nani_pix_bot.models.enums import (
    EventType,
    GameStatus,
    ItemSource,
    OutboxKind,
    SeasonStatus,
)
from nani_pix_bot.models.game import Game
from nani_pix_bot.models.season import SeasonSchedule
from nani_pix_bot.seasons import registry
from nani_pix_bot.seasons.definition import SeasonRun
from nani_pix_bot.seasons.mechanics import BoneSet
from nani_pix_bot.services import players
from nani_pix_bot.services.achievements import outbox
from nani_pix_bot.services.cosmetics import inventory
from nani_pix_bot.services.events import LoggedEvent
from nani_pix_bot.services.seasons import haunting


def count(session: Session, season_id: int, player_id: int) -> int:
    """The player's wins of the season's haunted or boss games."""
    stmt = select(func.count(Game.id)).where(
        Game.season_id == season_id,
        Game.status == GameStatus.WON,
        Game.winner_id == player_id,
        or_(Game.haunt_twist.is_not(None), Game.boss_game.is_(True)),
    )
    return session.scalar(stmt) or 0


def _already_queued(session: Session, season_id: int, player_id: int, bones: int) -> bool:
    stmt = select(AnnouncementOutbox).where(AnnouncementOutbox.kind == OutboxKind.BONE_FOUND)
    return any(
        row.payload.get("season_id") == season_id
        and row.payload.get("player_id") == player_id
        and row.payload.get("bones") == bones
        for row in session.scalars(stmt)
    )


def _bone_season(
    session: Session, event: LoggedEvent
) -> tuple[SeasonSchedule, SeasonRun, BoneSet] | None:
    """The running season, its run and bone set, when the event is a win of one
    of its haunted games."""
    if event.event_type != EventType.GAME_WON or event.actor_id is None or event.game_id is None:
        return None
    game = session.get(Game, event.game_id)
    if game is None or game.season_id is None or not haunting.is_haunted(game):
        return None
    season = session.get(SeasonSchedule, game.season_id)
    run = registry.get(season.run_id) if season is not None else None
    if season is None or run is None or run.bone_set is None:
        return None
    if season.status not in (SeasonStatus.ACTIVE, SeasonStatus.CLOSING):
        return None
    return season, run, run.bone_set


def on_game_won(session: Session, event: LoggedEvent) -> None:
    found = _bone_season(session, event)
    if found is None or event.actor_id is None:
        return
    season, run, bone_set = found
    target = bone_set.bones
    player_id = event.actor_id
    bones = count(session, season.id, player_id)
    item_id = f"{run.run_id}/{bone_set.item_key}"
    granted = bones >= target and inventory.grant(
        session, player_id, inventory.ItemGrant(item_id, ItemSource.BONES, season.id)
    )
    if bones > target and not granted:
        return
    if _already_queued(session, season.id, player_id, bones):
        return
    payload = {"season_id": season.id, "player_id": player_id, "bones": bones, "granted": granted}
    outbox.enqueue_goal(session, OutboxKind.BONE_FOUND, payload)
    logger.info(
        "bone {bones}/{target} for {recipient}",
        bones=bones,
        target=target,
        recipient=players.describe_player_id(session, player_id),
        recipient_id=player_id,
        granted=granted,
        season_id=season.id,
        run_id=run.run_id,
    )
