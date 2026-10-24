"""Haunted rounds (Halloween spec, "Yōkai Night"): the roll at activation,
the stored twist, and which twist a stage wears. Must not import
services.game (activate_game calls in here)."""

import random
from collections.abc import Sequence
from datetime import datetime
from typing import Protocol

from loguru import logger
from sqlalchemy.orm import Session

from nani_pix_bot.models.game import Game
from nani_pix_bot.seasons.definition import SeasonRun
from nani_pix_bot.seasons.mechanics import HauntTwist
from nani_pix_bot.services.seasons import event_day, modifiers

# Twists that clear up at the last stage; the others stay until the reveal.
FADES_AT_LAST: frozenset[HauntTwist] = frozenset({HauntTwist.TANUKI_LEAF, HauntTwist.KYOKOTSU_XRAY})

_RNG = random.Random()  # noqa: S311 - a game's flavour, not a secret


class _Rng(Protocol):
    def randrange(self, stop: int, /) -> int: ...
    def choice(self, seq: Sequence[HauntTwist], /) -> HauntTwist: ...


def _rng() -> random.Random:
    return _RNG


class _Recording:
    """Wraps an rng and remembers the last number drawn, for the log line."""

    def __init__(self, rng: _Rng) -> None:
        self._rng = rng
        self.number = -1

    def randrange(self, stop: int, /) -> int:
        self.number = self._rng.randrange(stop)
        return self.number

    def choice(self, seq: Sequence[HauntTwist], /) -> HauntTwist:
        return self._rng.choice(seq)


def roll(run: SeasonRun, *, event_day: bool, rng: _Rng) -> HauntTwist | None:
    """A twist, or None for a clean game. The 0-99 number is always drawn."""
    if run.haunt is None:
        return None
    number = rng.randrange(100)
    all_haunted = event_day and run.event_day is not None and run.event_day.all_haunted
    if all_haunted or number < run.haunt.chance_pct:
        return rng.choice(run.haunt.twists)
    return None


def on_activation(session: Session, game: Game, now: datetime, rng: _Rng | None = None) -> None:
    """Set the game's event-day flag and roll its twist, once, at activation."""
    run = modifiers.season_of(session, game)
    if run is None:
        return
    window = event_day.window_for_game(session, game)
    game.event_day = window is not None and window[0] <= now < window[1]
    if game.event_day and run.event_day is not None:
        logger.info(
            "activated on {event_day_name}",
            event_day_name=run.event_day.names["EN"],
            game_id=game.id,
        )
    if run.haunt is None:
        return
    drawn = _Recording(rng or _rng())
    twist = roll(run, event_day=game.event_day, rng=drawn)
    game.haunt_twist = None if twist is None else twist.value
    if twist is None:
        logger.info(
            "not haunted (rolled {roll}, chance {chance})",
            roll=drawn.number,
            chance=run.haunt.chance_pct,
            event_day=game.event_day,
            game_id=game.id,
        )
    else:
        logger.info(
            "haunted — {twist}",
            twist=twist.value,
            roll=drawn.number,
            chance=run.haunt.chance_pct,
            event_day=game.event_day,
            game_id=game.id,
        )


def stored_twist(game: Game) -> HauntTwist | None:
    if game.haunt_twist is None:
        return None
    try:
        return HauntTwist(game.haunt_twist)
    except ValueError:
        logger.warning(
            "unknown stored twist {twist!r} — rendering the game untwisted",
            twist=game.haunt_twist,
            game_id=game.id,
        )
        return None


def is_haunted(game: Game) -> bool:
    return game.boss_game or stored_twist(game) is not None


def twist_for_stage(run: SeasonRun | None, game: Game, stage: int) -> HauntTwist | None:
    """The twist a stage (1-based) wears; a boss game cycles the run's twists."""
    if run is None or run.haunt is None:
        return None
    if game.boss_game:
        return run.haunt.twists[(stage - 1) % len(run.haunt.twists)]
    return stored_twist(game)
