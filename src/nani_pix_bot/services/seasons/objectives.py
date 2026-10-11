"""Season objectives (seasons spec §5): one-time goals over the season's own
events (season_history.py) that pay Season XP and 🪙 once reached."""

from dataclasses import dataclass
from datetime import datetime

from loguru import logger
from sqlalchemy import select
from sqlalchemy.orm import Session

from nani_pix_bot.models.enums import CurrencyReason, XpSource
from nani_pix_bot.models.game import Game
from nani_pix_bot.models.player import Player
from nani_pix_bot.models.season import SeasonObjective, SeasonSchedule, SeasonXp
from nani_pix_bot.seasons import registry
from nani_pix_bot.seasons.definition import Objective, SeasonRun
from nani_pix_bot.services import players
from nani_pix_bot.services.achievements import outbox
from nani_pix_bot.services.economy import wallet
from nani_pix_bot.services.economy.wallet import LedgerEntry
from nani_pix_bot.services.events import LoggedEvent, as_utc
from nani_pix_bot.services.seasons import goals, schedule, season_history, season_pass


def _season_of(session: Session, event: LoggedEvent) -> SeasonSchedule | None:
    """Rule D1: a game's events belong to its season while that season still
    grants; a game-less event (a tip) to the season running now."""
    if event.game_id is None:
        return schedule.active(session)
    game = session.get(Game, event.game_id)
    if game is None or game.season_id is None:
        return None
    season = session.get(SeasonSchedule, game.season_id)
    if season is None or season.status not in season_pass.GRANTING:
        return None
    return season


def done_keys(session: Session, season_id: int, player_id: int) -> set[str]:
    stmt = select(SeasonObjective.objective_key).where(
        SeasonObjective.season_id == season_id, SeasonObjective.player_id == player_id
    )
    return set(session.scalars(stmt))


def _due(run: SeasonRun, done: set[str], event: LoggedEvent) -> list[Objective]:
    """The objectives still open that this event could move."""
    return [
        o
        for o in run.objectives
        if o.key not in done and event.event_type in goals.TRIGGERS[o.goal.kind]
    ]


@dataclass(frozen=True)
class _Open:
    """An event's season and run, its actor, and the objectives it could complete."""

    season: SeasonSchedule
    run: SeasonRun
    player: Player
    game_id: int | None
    due: list[Objective]


def _complete(session: Session, found: _Open, objective: Objective) -> None:
    season, player = found.season, found.player
    pid = player.telegram_user_id
    session.add(SeasonObjective(season_id=season.id, player_id=pid, objective_key=objective.key))
    session.add(
        SeasonXp(
            season_id=season.id,
            player_id=pid,
            amount=objective.xp,
            source=XpSource.OBJECTIVE,
            game_id=found.game_id,
        )
    )
    if objective.coins:
        entry = LedgerEntry(CurrencyReason.SEASON_OBJECTIVE, season_id=season.id, coins=True)
        wallet.credit(session, player, objective.coins, entry)
    outbox.enqueue_objective(session, season.id, pid, objective.key)
    session.flush()
    logger.info(
        "{player} completed the season objective {objective} (+{xp} XP, +{coins} 🪙)",
        player=players.describe_player_id(session, pid),
        player_id=pid,
        objective=objective.key,
        xp=objective.xp,
        coins=objective.coins,
        season_id=season.id,
        run_id=found.run.run_id,
    )


def _open_for(session: Session, event: LoggedEvent, player: Player) -> _Open | None:
    season = _season_of(session, event)
    run = registry.get(season.run_id) if season is not None else None
    if season is None or run is None or not run.objectives:
        return None
    due = _due(run, done_keys(session, season.id, player.telegram_user_id), event)
    return _Open(season, run, player, event.game_id, due) if due else None


def check(session: Session, event: LoggedEvent) -> list[Objective]:
    """The objectives `event`'s actor has just completed, paid and announced."""
    # The house (actor None) completes nothing: the 🪙 credits below and the
    # pass sync's rewards emit CURRENCY_MOVED from it, so this never re-enters.
    if event.actor_id is None:
        return []
    player = session.get(Player, event.actor_id)
    found = _open_for(session, event, player) if player is not None else None
    if found is None:
        return []
    pid = found.player.telegram_user_id
    history = season_history.for_player(session, found.season, pid)
    reached = [o for o in found.due if goals.progress(o.goal, history) >= o.goal.target]
    for objective in reached:
        _complete(session, found, objective)
    if reached:
        season_pass.sync(session, found.season, found.run, pid)
    return reached


@dataclass(frozen=True)
class ObjectiveStatus:
    """One objective as a view shows it: progress capped at the target, and
    when it was completed (None while open)."""

    objective: Objective
    progress: int
    completed_at: datetime | None

    @property
    def done(self) -> bool:
        return self.completed_at is not None


def _completed(session: Session, season_id: int, player_id: int) -> dict[str, datetime]:
    stmt = select(SeasonObjective.objective_key, SeasonObjective.completed_at).where(
        SeasonObjective.season_id == season_id, SeasonObjective.player_id == player_id
    )
    return {key: as_utc(at) for key, at in session.execute(stmt)}


def status(
    session: Session, season: SeasonSchedule, run: SeasonRun, player_id: int
) -> list[ObjectiveStatus]:
    """Every objective of `run` with the player's progress toward it."""
    completed = _completed(session, season.id, player_id)
    history = season_history.for_player(session, season, player_id)
    rows = []
    for o in run.objectives:
        at = completed.get(o.key)
        reached = o.goal.target if at is not None else goals.progress(o.goal, history)
        rows.append(ObjectiveStatus(o, min(o.goal.target, reached), at))
    return rows
