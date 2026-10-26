"""The 100 Candles group goal (Halloween spec): which finished season games
light a candle, who contributed, and which milestone is due. Pure reads; must
not import services.game."""

from sqlalchemy import select
from sqlalchemy.orm import Session

from nani_pix_bot.models.enums import GameStatus
from nani_pix_bot.models.game import Game
from nani_pix_bot.models.game_guess import GameGuess
from nani_pix_bot.models.season import SeasonSchedule
from nani_pix_bot.seasons import registry
from nani_pix_bot.seasons.definition import SeasonRun
from nani_pix_bot.seasons.mechanics import GroupGoal

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


def candles(session: Session, season_id: int, goal: GroupGoal) -> int:
    rows = session.execute(
        select(Game.event_day, Game.haunt_twist, Game.boss_game).where(
            Game.season_id == season_id, Game.status.in_(SNUFFING)
        )
    )
    return sum(_weight(day, twist is not None, boss, goal) for day, twist, boss in rows)


def contributors(session: Session, season_id: int) -> set[int]:
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
        ids.update(session.scalars(query))
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
