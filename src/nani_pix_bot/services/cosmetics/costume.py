"""Which costume Nani Pix wears on a game's pictures (Halloween spec).

Order: the starter's equipped costume item, else the default of the game's
season run, else plain. Read live at each render, like the host frame."""

from loguru import logger
from sqlalchemy.orm import Session

from nani_pix_bot.models.enums import CosmeticSlot, GameStatus
from nani_pix_bot.models.game import Game
from nani_pix_bot.seasons import registry
from nani_pix_bot.seasons.definition import SeasonRun
from nani_pix_bot.services.cosmetics import inventory, looks
from nani_pix_bot.services.seasons import modifiers, schedule


def active_run(session: Session) -> SeasonRun | None:
    season = schedule.active(session)
    return None if season is None else registry.get(season.run_id)


def for_player(session: Session, player_id: int | None, run: SeasonRun | None) -> str | None:
    equipped = (
        None
        if player_id is None
        else inventory.look(session, player_id, CosmeticSlot.MASCOT_COSTUME)
    )
    if equipped is not None:
        costume, source = equipped, "equipped"
    elif run is not None and run.mascot_costume is not None:
        costume, source = run.mascot_costume, "run"
    else:
        costume, source = None, "plain"
    logger.debug("Nani's costume: {costume} ({source})", costume=costume, source=source)
    return costume


def for_game(session: Session, game: Game) -> str | None:
    run = (
        active_run(session)
        if game.status is GameStatus.SETUP
        else modifiers.season_of(session, game)
    )
    return for_player(session, game.starter_id, run)


def for_game_id(session: Session, game_id: int) -> str | None:
    game = session.get(Game, game_id)
    return None if game is None else for_game(session, game)


def for_viewer(session: Session, player_id: int) -> str | None:
    return for_player(session, player_id, active_run(session))


def problems(run: SeasonRun) -> list[str]:
    if run.mascot_costume is None:
        return []
    return [f"mascot_costume: {p}" for p in looks.costume_problems(run.mascot_costume)]
