"""The champion-score lines a win message gets (spec §6): what the win added
to the winner's week, month and year totals, and the host's +1. Scoring is
periods.py's; this only words it."""

from collections.abc import Sequence
from datetime import datetime

from sqlalchemy.orm import Session

from nani_pix_bot.services import i18n, players, settings
from nani_pix_bot.services.achievements import periods
from nani_pix_bot.services.achievements.periods import PeriodGain


def _marker(gain: PeriodGain, lang: str) -> str:
    if gain.rank_before is None:
        return i18n.t("champion.win.new", lang)
    moved = gain.rank_before - gain.rank_after
    if moved > 0:
        return i18n.t("champion.win.up", lang, places=moved)
    return i18n.t("champion.win.same", lang)  # a score only grows; never "down"


def _part(gain: PeriodGain, lang: str) -> str:
    return i18n.t(
        "champion.win.part",
        lang,
        period=i18n.t(f"champion.win.period.{gain.period.type.value}", lang),
        score=gain.score,
        rank=gain.rank_after,
        marker=_marker(gain, lang),
    )


def gain_line(gains: Sequence[PeriodGain], lang: str) -> str:
    """'+5 🌟 → week 14 (#1 ⬆2) · month 31 (#2 =)'; '' with no period."""
    if not gains:
        return ""
    parts = i18n.t("champion.win.join", lang).join(_part(g, lang) for g in gains)
    return i18n.t("champion.win.gain", lang, gain=gains[0].gain, parts=parts)


def _host_line(session: Session, game_id: int, lang: str) -> str:
    won = periods.win_event(session, game_id)
    if won is None or won.data["hard_mode"] or won.subject_id in (None, won.actor_id):
        return ""
    host = players.display_name(session, won.subject_id)
    return i18n.t("champion.win.host", lang, host=host, gain=periods.HOST_POINTS)


def win_lines(session: Session, game_id: int, lang: str, now: datetime) -> list[str]:
    """The lines for a game that has just been won; [] for any other game,
    and for a win that fell in none of the periods running at `now`."""
    tz = settings.get_group_timezone(session)
    gains = periods.win_gains(session, game_id, now, tz)
    if not gains:
        return []
    lines = [gain_line(gains, lang), _host_line(session, game_id, lang)]
    return [line for line in lines if line]
