"""A finished period's podium as text lines and as a PodiumCard (spec §6, §7)."""

from sqlalchemy import select
from sqlalchemy.orm import Session

from nani_pix_bot.models.period import PeriodResult
from nani_pix_bot.services import i18n, players
from nani_pix_bot.services.cards import PodiumCard, PodiumEntry

_MEDALS = ("🥇", "🥈", "🥉")


def results(session: Session, ptype: str, key: str) -> list[PeriodResult]:
    stmt = (
        select(PeriodResult)
        .where(PeriodResult.period_type == ptype, PeriodResult.period_key == key)
        # Tied places share a rank; finalize stored them in display order.
        .order_by(PeriodResult.rank, PeriodResult.id)
    )
    return list(session.scalars(stmt))


def lines(session: Session, placed: list[PeriodResult], lang: str) -> list[str]:
    return [
        i18n.t(
            "period.summary.row",
            lang,
            medal=_MEDALS[r.rank - 1],
            player=players.display_name(session, r.player_id),
            score=r.score,
            wins=r.wins,
        )
        for r in placed
    ]


def card(session: Session, placed: list[PeriodResult], title: str, lang: str) -> PodiumCard:
    """Every place, positioned by rank on the card (co-champions share the
    large #1 styling)."""
    entries = tuple(
        PodiumEntry(
            players.display_name(session, r.player_id),
            i18n.t("card.score", lang, score=r.score, wins=r.wins),
            r.player_id,
            r.rank,
        )
        for r in placed
    )
    return PodiumCard(title, entries)
