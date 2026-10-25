"""Who a player may hex right now, for the /hex player picker: everyone who played
in the season except the caster, the opted-out and the already hexed, most active first."""

from datetime import datetime

from sqlalchemy import or_, select, union
from sqlalchemy.orm import Session

from nani_pix_bot.models.game import Game
from nani_pix_bot.models.game_guess import GameGuess
from nani_pix_bot.models.halloween import Hex
from nani_pix_bot.models.player import Player
from nani_pix_bot.models.season import SeasonSchedule
from nani_pix_bot.services import players
from nani_pix_bot.services.seasons.hex_marks import LIVE_STATUSES, naive_utc


def _played(season_id: int):
    """Everyone `hexes.played_in_season` accepts, in one query."""
    guessed = (
        select(GameGuess.player_id.label("player_id"))
        .join(Game, Game.id == GameGuess.game_id)
        .where(Game.season_id == season_id)
    )
    hosted = select(Game.starter_id.label("player_id")).where(
        Game.season_id == season_id,
        Game.activated_at.is_not(None),
        Game.hard_mode.is_(False),
    )
    return union(guessed, hosted).subquery()


def _hexed(now: datetime):
    """Everyone `hex_marks.active_hex_for` finds a hex on."""
    return (
        select(Hex.target_id)
        .join(SeasonSchedule, SeasonSchedule.id == Hex.season_id)
        .where(
            Hex.resolved_at.is_(None),
            SeasonSchedule.status.in_(LIVE_STATUSES),
            or_(Hex.expires_at.is_(None), Hex.expires_at > naive_utc(now)),
        )
    )


def hex_targets(
    session: Session, season: SeasonSchedule, caster_id: int, now: datetime
) -> list[Player]:
    """The players `caster_id` could hex now, most active in the season first."""
    played = _played(season.id)
    stmt = select(Player).where(
        Player.telegram_user_id.in_(select(played.c.player_id)),
        Player.telegram_user_id != caster_id,
        Player.hex_opt_out.is_(False),
        Player.telegram_user_id.not_in(_hexed(now)),
    )
    found = {player.telegram_user_id: player for player in session.scalars(stmt)}
    order = players.by_activity(session, list(found), naive_utc(season.start_at))
    return [found[player_id] for player_id in order]
