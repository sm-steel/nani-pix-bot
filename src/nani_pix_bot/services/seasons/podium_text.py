"""The season-end post's standings lines (seasons spec §6)."""

from sqlalchemy.orm import Session

from nani_pix_bot.services import i18n, players
from nani_pix_bot.services.seasons import lifecycle

PODIUM_RANKS = 3  # the post lists everyone ranked 1st to 3rd


def lines(session: Session, season_id: int, lang: str) -> list[str]:
    """One line per player ranked 1-3 — by rank, not by row count, so a
    player tied for 3rd still stands on the podium; a single "nobody
    scored" line when the season froze with no results."""
    placed = [p for p in lifecycle.podium(session, season_id) if p.rank <= PODIUM_RANKS]
    if not placed:
        return [i18n.t("season.post.no_scores", lang)]
    return [
        i18n.t(
            "season.post.podium_line",
            lang,
            rank=row.rank,
            player=players.display_name(session, row.player_id),
            xp=row.xp,
        )
        for row in placed
    ]
