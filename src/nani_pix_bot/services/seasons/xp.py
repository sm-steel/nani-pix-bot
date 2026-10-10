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
"""

from dataclasses import dataclass

from loguru import logger
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from nani_pix_bot.models.enums import EventType, XpSource
from nani_pix_bot.models.game import Game
from nani_pix_bot.models.game_guess import GameGuess
from nani_pix_bot.models.season import SeasonSchedule, SeasonXp
from nani_pix_bot.seasons import registry
from nani_pix_bot.seasons.definition import XpTable
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


def on_event(session: Session, event: LoggedEvent) -> None:
    if event.event_type not in _HANDLED or event.game_id is None:
        return
    game = session.get(Game, event.game_id)
    if game is None or game.season_id is None:
        return
    season = session.get(SeasonSchedule, game.season_id)
    run = registry.get(season.run_id) if season is not None else None
    if season is None or run is None:
        logger.error(
            "game tagged to season {season_id} with no loadable run — no XP",
            season_id=game.season_id,
            game_id=game.id,
        )
        return
    first = event.event_type == EventType.GUESS and _is_first_guess(
        session, game.id, event.actor_id
    )
    for award in awards(run.xp, event, bot_game=game.hard_mode, first_guess_of_player=first):
        session.add(
            SeasonXp(
                season_id=season.id,
                player_id=award.player_id,
                amount=award.amount,
                source=award.source,
                game_id=game.id,
            )
        )
        logger.info(
            "season XP +{amount} ({source}) to {recipient}",
            amount=award.amount,
            source=award.source.value,
            recipient=players.describe_player_id(session, award.player_id),
            recipient_id=award.player_id,
            season_id=season.id,
            run_id=season.run_id,
            game_id=game.id,
        )
    session.flush()


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
