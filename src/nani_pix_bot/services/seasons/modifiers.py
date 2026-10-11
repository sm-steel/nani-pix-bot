"""Rule modifiers of the season a game is tagged to (seasons spec §5),
read where the amounts they change are computed."""

from collections.abc import Mapping
from types import MappingProxyType

from loguru import logger
from sqlalchemy.orm import Session

from nani_pix_bot.models.game import Game
from nani_pix_bot.models.season import SeasonSchedule
from nani_pix_bot.seasons import registry
from nani_pix_bot.seasons.definition import Modifier, SeasonRun

_NONE: Mapping[Modifier, int] = MappingProxyType({})


def season_of(session: Session, game: Game) -> SeasonRun | None:
    if game.season_id is None:
        return None
    season = session.get(SeasonSchedule, game.season_id)
    if season is None:
        return None
    run = registry.get(season.run_id)
    if run is None:
        logger.warning(
            "season {season_id}'s run {run_id!r} is not registered — playing it as a plain game",
            season_id=season.id,
            run_id=season.run_id,
            game_id=game.id,
        )
    return run


def for_game(session: Session, game: Game) -> Mapping[Modifier, int]:
    run = season_of(session, game)
    return run.modifiers if run is not None else _NONE
