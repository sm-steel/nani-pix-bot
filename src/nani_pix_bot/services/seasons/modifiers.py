"""Rule modifiers of the season a game is tagged to (seasons spec §5),
read where the amounts they change are computed."""

from collections.abc import Mapping
from types import MappingProxyType

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
    return registry.get(season.run_id) if season is not None else None


def for_game(session: Session, game: Game) -> Mapping[Modifier, int]:
    run = season_of(session, game)
    return run.modifiers if run is not None else _NONE
