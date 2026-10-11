"""Season XP (seasons spec §2, §7): what each game event pays, written as
season_xp rows for games tagged to a season.

Design notes:
- Winner XP uses the `stage` from the `game_won` event (`win_facts.stage_number`:
  1-5, or the HARD MODE turn 1-2), clamped to 1-5; 0 (unknown) gives no win XP.
- Host XP goes to the starter (`subject_id`) — except on HARD MODE games, which
  only the bot starts (`jobs/timers/autostart.py` is the only place that sets
  `hard_mode = True`).
- "First guess" = the player's first `game_guesses` row in that game (existing
  domain state; `log_guess` adds the row before it emits `GUESS`).
- A `/setwinner` on a game that already ended unsolved (an earlier
  `game_unsolved` event for it) already paid the host `host_unsolved`: its
  `game_won` tops the host up to `host_solved` (never below 0), so the host
  ends with exactly a solved game's award. The winner's award is unchanged.
"""

from dataclasses import dataclass, replace

from loguru import logger
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from nani_pix_bot.models.enums import EventType, SeasonStatus, XpSource
from nani_pix_bot.models.event_log import EventLog
from nani_pix_bot.models.game import Game
from nani_pix_bot.models.game_guess import GameGuess
from nani_pix_bot.models.season import SeasonSchedule, SeasonXp
from nani_pix_bot.seasons import registry
from nani_pix_bot.seasons.definition import SeasonRun, XpTable
from nani_pix_bot.services import players
from nani_pix_bot.services.events import LoggedEvent

_HANDLED = frozenset({EventType.GAME_WON, EventType.GAME_UNSOLVED, EventType.GUESS})


@dataclass(frozen=True)
class Award:
    player_id: int
    amount: int
    source: XpSource


@dataclass(frozen=True)
class Placed:
    rank: int
    player_id: int
    xp: int


def _win_xp(table: XpTable, stage: int) -> int:
    if stage < 1:
        return 0
    return table.win_by_stage[min(stage, len(table.win_by_stage)) - 1]


def host_top_up(table: XpTable) -> XpTable:
    """`table` for a win after an unsolved end that already paid the host
    `host_unsolved`: the host's solved award is only the rest (never below 0)."""
    return replace(table, host_solved=max(table.host_solved - table.host_unsolved, 0))


def payable(table: XpTable, found: "Tagged") -> XpTable:
    return host_top_up(table) if found.host_paid_unsolved else table


def awards(
    table: XpTable, event: LoggedEvent, *, bot_game: bool, first_guess_of_player: bool
) -> list[Award]:
    found: list[Award] = []
    if event.event_type == EventType.GAME_WON and event.actor_id is not None:
        win = _win_xp(table, int(event.data.get("stage", 0)))
        if win:
            found.append(Award(event.actor_id, win, XpSource.WIN))
        if not bot_game and event.subject_id is not None and table.host_solved:
            found.append(Award(event.subject_id, table.host_solved, XpSource.HOST))
    elif event.event_type == EventType.GAME_UNSOLVED:
        if not bot_game and event.subject_id is not None and table.host_unsolved:
            found.append(Award(event.subject_id, table.host_unsolved, XpSource.HOST))
    elif (
        event.event_type == EventType.GUESS
        and event.actor_id is not None
        and first_guess_of_player
        and table.first_guess
    ):
        found.append(Award(event.actor_id, table.first_guess, XpSource.FIRST_GUESS))
    return found


def _is_first_guess(session: Session, game_id: int, player_id: int | None) -> bool:
    if player_id is None:
        return False
    stmt = select(func.count(GameGuess.id)).where(
        GameGuess.game_id == game_id, GameGuess.player_id == player_id
    )
    return session.scalar(stmt) == 1


def _ended_unsolved_before(session: Session, event: LoggedEvent, game_id: int) -> bool:
    """A GAME_WON for a game that already ended unsolved: a /setwinner."""
    if event.event_type != EventType.GAME_WON:
        return False
    stmt = select(func.count(EventLog.id)).where(
        EventLog.game_id == game_id,
        EventLog.event_type == EventType.GAME_UNSOLVED,
        EventLog.id < event.id,
    )
    return bool(session.scalar(stmt))


@dataclass(frozen=True)
class Tagged:
    """A game event that counts for a season: the game, its open season and run,
    whether a GUESS was the player's first in the game, and whether a GAME_WON
    follows an unsolved end that already paid the host (a /setwinner)."""

    game: Game
    season: SeasonSchedule
    run: SeasonRun
    first_guess: bool
    host_paid_unsolved: bool = False


def tagged(session: Session, event: LoggedEvent) -> Tagged | None:
    """The season `event` pays into, or None (not a paying event, untagged game,
    unloadable run, or the season already over — each logged as today)."""
    if event.event_type not in _HANDLED or event.game_id is None:
        return None
    game = session.get(Game, event.game_id)
    if game is None or game.season_id is None:
        return None
    season = session.get(SeasonSchedule, game.season_id)
    run = registry.get(season.run_id) if season is not None else None
    if season is None or run is None:
        logger.error(
            "game tagged to season {season_id} with no loadable run — no XP",
            season_id=game.season_id,
            game_id=game.id,
        )
        return None
    if season.status in (SeasonStatus.ENDED, SeasonStatus.CANCELLED):
        # e.g. /setwinner re-finishing a tagged game after the podium froze:
        # paying now would make the ledger disagree with season_results.
        logger.info(
            "season {run_id} is already over — no XP for this {event_type} event",
            run_id=season.run_id,
            event_type=event.event_type.value,
            season_id=season.id,
            game_id=game.id,
        )
        return None
    first = event.event_type == EventType.GUESS and _is_first_guess(
        session, game.id, event.actor_id
    )
    return Tagged(game, season, run, first, _ended_unsolved_before(session, event, game.id))


def pay(session: Session, event: LoggedEvent, found: Tagged) -> list[Award]:
    """Season XP for `event`: season_xp rows, returned."""
    paid = awards(
        payable(found.run.xp, found),
        event,
        bot_game=found.game.hard_mode,
        first_guess_of_player=found.first_guess,
    )
    for award in paid:
        session.add(
            SeasonXp(
                season_id=found.season.id,
                player_id=award.player_id,
                amount=award.amount,
                source=award.source,
                game_id=found.game.id,
            )
        )
        logger.info(
            "season XP +{amount} ({source}) to {recipient}",
            amount=award.amount,
            source=award.source.value,
            recipient=players.describe_player_id(session, award.player_id),
            recipient_id=award.player_id,
            season_id=found.season.id,
            run_id=found.season.run_id,
            game_id=found.game.id,
        )
    session.flush()
    return paid


def standings(session: Session, season_id: int) -> list[Placed]:
    """Competition ranks by total XP (1, 1, 3); ties keep player-id order."""
    total = func.sum(SeasonXp.amount)
    rows = session.execute(
        select(SeasonXp.player_id, total)
        .where(SeasonXp.season_id == season_id)
        .group_by(SeasonXp.player_id)
        .order_by(total.desc(), SeasonXp.player_id)
    ).all()
    placed: list[Placed] = []
    for index, (player_id, amount) in enumerate(rows):
        rank = placed[-1].rank if placed and placed[-1].xp == amount else index + 1
        placed.append(Placed(rank, player_id, int(amount)))
    return placed
