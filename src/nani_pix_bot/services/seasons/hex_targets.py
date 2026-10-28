"""Who a player may hex right now, for the /hex player picker: everyone who played
in the season except the caster, the opted-out and the already hexed, most active first."""

from datetime import datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from nani_pix_bot.models.halloween import Hex
from nani_pix_bot.models.player import Player
from nani_pix_bot.models.season import SeasonSchedule
from nani_pix_bot.services import players
from nani_pix_bot.services.seasons.hex_marks import live_hexes, naive_utc
from nani_pix_bot.services.seasons.hexes import played_ids


def hex_targets(
    session: Session, season: SeasonSchedule, caster_id: int, now: datetime
) -> list[Player]:
    """The players `caster_id` could hex now, most active in the season first."""
    played = played_ids(season.id)
    hexed = live_hexes(now).with_only_columns(Hex.target_id, maintain_column_froms=True)
    stmt = select(Player).where(
        Player.telegram_user_id.in_(select(played.c.player_id)),
        Player.telegram_user_id != caster_id,
        Player.hex_opt_out.is_(False),
        Player.telegram_user_id.not_in(hexed),
    )
    found = {player.telegram_user_id: player for player in session.scalars(stmt)}
    order = players.by_activity(session, list(found), naive_utc(season.start_at))
    return [found[player_id] for player_id in order]
