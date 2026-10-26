"""The 100 Candles group goal (Halloween spec): which finished season games
light a candle, who contributed, which milestone is due, the one-time
payout when the target is reached, and when the Gashadokuro boss game is due.
Must not import services.game."""

from loguru import logger
from sqlalchemy import Select, select
from sqlalchemy.orm import Session

from nani_pix_bot.models.announcement import AnnouncementOutbox
from nani_pix_bot.models.enums import (
    CurrencyReason,
    EventType,
    GameStatus,
    ItemSource,
    OutboxKind,
    SeasonStatus,
)
from nani_pix_bot.models.event_log import EventLog
from nani_pix_bot.models.game import Game
from nani_pix_bot.models.game_guess import GameGuess
from nani_pix_bot.models.player import Player
from nani_pix_bot.models.season import SeasonSchedule
from nani_pix_bot.seasons import registry
from nani_pix_bot.seasons.definition import SeasonRun
from nani_pix_bot.seasons.mechanics import GroupGoal
from nani_pix_bot.services import events, players
from nani_pix_bot.services.achievements import outbox
from nani_pix_bot.services.cosmetics import inventory
from nani_pix_bot.services.economy import wallet
from nani_pix_bot.services.events import LoggedEvent
from nani_pix_bot.services.seasons import schedule

SNUFFING: tuple[GameStatus, ...] = (GameStatus.WON, GameStatus.UNSOLVED)


def _weight(event_day: bool, haunted: bool, boss: bool, goal: GroupGoal) -> int:
    if boss:
        return 0
    if event_day:
        return goal.per_event_day
    if haunted:
        return goal.per_haunted
    return goal.per_game


def candles_for(game: Game, goal: GroupGoal) -> int:
    if game.status not in SNUFFING:
        return 0
    return _weight(game.event_day, game.haunt_twist is not None, game.boss_game, goal)


def _read(stmt: Select, lock: bool) -> Select:
    """`lock` makes it a locking read: InnoDB serves those from the latest committed
    rows, not the REPEATABLE READ snapshot, so a waiter sees the winner's writes
    (a no-op on SQLite)."""
    return stmt.with_for_update() if lock else stmt


def candles(session: Session, season_id: int, goal: GroupGoal, *, lock: bool = False) -> int:
    stmt = select(Game.event_day, Game.haunt_twist, Game.boss_game).where(
        Game.season_id == season_id, Game.status.in_(SNUFFING)
    )
    rows = session.execute(_read(stmt, lock))
    return sum(_weight(day, twist is not None, boss, goal) for day, twist, boss in rows)


def contributors(session: Session, season_id: int, *, lock: bool = False) -> set[int]:
    """Guessers, non-hard-mode hosts and winners of the season's finished, non-boss games."""
    finished = (
        Game.season_id == season_id,
        Game.status.in_(SNUFFING),
        Game.boss_game.is_(False),
    )
    guessers = select(GameGuess.player_id).join(Game, Game.id == GameGuess.game_id).where(*finished)
    hosts = select(Game.starter_id).where(*finished, Game.hard_mode.is_(False))
    winners = select(Game.winner_id).where(*finished, Game.winner_id.is_not(None))
    ids = set()
    for query in (guessers, hosts, winners):
        ids.update(session.scalars(_read(query, lock)))
    return ids


def is_contributor(session: Session, season_id: int, player_id: int) -> bool:
    return player_id in contributors(session, season_id)


def due_milestone(snuffed: int, goal: GroupGoal, last_queued: int) -> int | None:
    """The largest milestone below the target that `snuffed` has reached and
    that has not been queued yet."""
    every = goal.milestone_every
    if every <= 0:
        return None
    due = [m for m in range(every, goal.target, every) if last_queued < m <= snuffed]
    return max(due) if due else None


def goal_of(
    session: Session, season_id: int | None
) -> tuple[SeasonSchedule, SeasonRun, GroupGoal] | None:
    if season_id is None:
        return None
    season = session.get(SeasonSchedule, season_id)
    if season is None:
        return None
    run = registry.get(season.run_id)
    if run is None or run.group_goal is None:
        return None
    return season, run, run.group_goal


def _last_queued(session: Session, season_id: int) -> int:
    """The highest milestone queued for the season, posted or not (a locking read)."""
    stmt = select(AnnouncementOutbox).where(AnnouncementOutbox.kind == OutboxKind.GOAL_MILESTONE)
    rows = session.scalars(_read(stmt, True))
    return max(
        (r.payload["milestone"] for r in rows if r.payload.get("season_id") == season_id), default=0
    )


def _lock_season(session: Session, season_id: int) -> None:
    """Lock the season row (a no-op on SQLite) and refresh it, so concurrent
    goal writers for one season run one after another."""
    session.execute(
        select(SeasonSchedule)
        .where(SeasonSchedule.id == season_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )


def is_settled(session: Session, season_id: int, *, lock: bool = False) -> bool:
    stmt = select(EventLog.data).where(EventLog.event_type == EventType.GOAL_REACHED)
    return any(data.get("season_id") == season_id for data in session.scalars(_read(stmt, lock)))


def _pay(session: Session, season: SeasonSchedule, run: SeasonRun, player_id: int) -> bool:
    goal = run.group_goal
    player = session.get(Player, player_id)
    if goal is None or player is None:
        logger.error("no player {recipient_id} to pay the goal reward", recipient_id=player_id)
        return False
    if goal.contributor_coins:
        entry = wallet.LedgerEntry(CurrencyReason.SEASON_GOAL, season_id=season.id, coins=True)
        wallet.credit(session, player, goal.contributor_coins, entry)
    if goal.contributor_item_key is not None:
        item_id = f"{run.run_id}/{goal.contributor_item_key}"
        grant = inventory.ItemGrant(item_id, ItemSource.GOAL, season.id)
        inventory.grant(session, player_id, grant)
    return True


def settle(
    session: Session, season: SeasonSchedule, run: SeasonRun, crossing_game_id: int | None
) -> bool:
    """Pays every contributor once and queues the one post. False when the
    season was already settled."""
    goal = run.group_goal
    if goal is None or is_settled(session, season.id, lock=True):
        return False
    snuffed = candles(session, season.id, goal, lock=True)
    paid = [
        p
        for p in sorted(contributors(session, season.id, lock=True))
        if _pay(session, season, run, p)
    ]
    data = {
        "season_id": season.id,
        "run_id": run.run_id,
        "crossing_game_id": crossing_game_id,
        "candles": snuffed,
        "coins": goal.contributor_coins,
    }
    for player_id in paid:
        events.emit(session, EventType.GOAL_REACHED, events.Involved(actor_id=player_id), **data)
    if not paid:
        logger.warning(
            "goal reached with no contributors — nothing to pay",
            season_id=season.id,
            run_id=run.run_id,
        )
        events.emit(session, EventType.GOAL_REACHED, events.Involved(actor_id=None), **data)
    queued = {"season_id": season.id, "players": len(paid), "candles": snuffed}
    outbox.enqueue_goal(session, OutboxKind.GOAL_REACHED, queued)
    logger.info(
        "goal reached: {candles} candles — {players} contributors get {coins} 🪙{item_note}",
        candles=snuffed,
        players=len(paid),
        coins=goal.contributor_coins,
        item=goal.contributor_item_key,
        item_note=f" and {goal.contributor_item_key}" if goal.contributor_item_key else "",
        season_id=season.id,
        run_id=run.run_id,
        recipients=[players.describe_player_id(session, p) for p in paid],
    )
    return True


def _queue_milestone(
    session: Session, season: SeasonSchedule, goal: GroupGoal, snuffed: int
) -> None:
    due = due_milestone(snuffed, goal, _last_queued(session, season.id))
    if due is None:
        return
    queued = {"season_id": season.id, "milestone": due}
    outbox.enqueue_goal(session, OutboxKind.GOAL_MILESTONE, queued)
    logger.info(
        "milestone {milestone} reached — {candles}/{target} candles",
        milestone=due,
        candles=snuffed,
        target=goal.target,
        season_id=season.id,
    )


def on_game_ended(session: Session, event: LoggedEvent) -> None:
    if event.event_type not in (EventType.GAME_WON, EventType.GAME_UNSOLVED):
        return
    game = session.get(Game, event.game_id) if event.game_id is not None else None
    found = goal_of(session, game.season_id if game is not None else None)
    if found is None:
        return
    season, run, goal = found
    if season.status not in (SeasonStatus.ACTIVE, SeasonStatus.CLOSING):
        return
    # Serialize concurrent game ends on the season row; everything below is
    # read after the lock is held.
    _lock_season(session, season.id)
    snuffed = candles(session, season.id, goal, lock=True)
    if snuffed < goal.target:
        _queue_milestone(session, season, goal, snuffed)
    else:
        settle(session, season, run, event.game_id)


def boss_game(session: Session, season_id: int, *, lock: bool = False) -> Game | None:
    """The season's boss game in any status, if one exists (a stopped boss is
    deleted, so it no longer counts)."""
    stmt = select(Game).where(Game.season_id == season_id, Game.boss_game.is_(True)).limit(1)
    return session.scalars(_read(stmt, lock)).first()


def _due_season(session: Session, *, lock: bool = False) -> SeasonSchedule | None:
    """The active season whose goal is settled and that has no boss game yet."""
    season = schedule.active(session)
    if season is None or goal_of(session, season.id) is None:
        return None
    if not is_settled(session, season.id, lock=lock):
        return None
    if boss_game(session, season.id, lock=lock) is not None:
        return None
    return season


def boss_due(session: Session) -> bool:
    return _due_season(session) is not None


def mark_boss(session: Session, game: Game) -> None:
    """Make `game` the season's boss game, once per season (call it before
    activate_game). A no-op, logged, when no boss is due any more."""
    season = schedule.active(session)
    if season is not None:
        # Serialize concurrent boss starts; the re-check below reads after the lock.
        _lock_season(session, season.id)
    season = _due_season(session, lock=True)
    if season is None:
        logger.warning("no boss game due — not marking this game the boss", game_id=game.id)
        return
    game.boss_game = True
    game.season_id = season.id
    session.flush()
    outbox.enqueue_goal(session, OutboxKind.GOAL_BOSS, {"season_id": season.id, "game_id": game.id})
    logger.info(
        "the Gashadokuro rises — boss game",
        season_id=season.id,
        run_id=season.run_id,
        game_id=game.id,
    )
