"""The champion-score lines a win message gets (spec §6): what the win added
to the winner's week, month and year totals, and the host's +1. Scoring is
periods.py's; this only words it."""

from collections.abc import Sequence
from datetime import datetime

from sqlalchemy.orm import Session

from nani_pix_bot.services import i18n, players, settings
from nani_pix_bot.services.achievements import periods
from nani_pix_bot.services.achievements.periods import PeriodGain


def _points(gain: int, lang: str) -> str:
    return i18n.t(f"champion.win.unit.{i18n.plural(gain, lang)}", lang)


def _marker(gain: PeriodGain, lang: str) -> str:
    if gain.rank_before is None:
        return i18n.t("champion.win.new", lang)
    moved = gain.rank_before - gain.rank_after
    if moved > 0:
        return i18n.t("champion.win.up", lang, places=moved)
    return ""  # unchanged — and a score only grows, so never "down"


def _part(gain: PeriodGain, lang: str) -> str:
    return i18n.t(
        "champion.win.part",
        lang,
        period=i18n.t(f"champion.win.period.{gain.period.type.value}", lang),
        score=gain.score,
        place=i18n.t(
            "champion.win.place_shared" if gain.shared else "champion.win.place",
            lang,
            ordinal=i18n.ordinal(gain.rank_after, lang),
        ),
        marker=_marker(gain, lang),
    )


def gain_line(gains: Sequence[PeriodGain], winner: str, lang: str) -> str:
    """'🌟 @a: +5 points' and one bullet per running period, e.g.
    '   • this week — 14 🌟, 1st place (⬆2)'; '' with no period."""
    if not gains:
        return ""
    gain = gains[0].gain
    header = i18n.t("champion.win.gain", lang, player=winner, gain=gain, unit=_points(gain, lang))
    return "\n".join([header, *(_part(g, lang) for g in gains)])


def _host_line(session: Session, game_id: int, lang: str) -> str:
    won = periods.win_event(session, game_id)
    if won is None or won.data["hard_mode"] or won.subject_id in (None, won.actor_id):
        return ""
    host = players.display_name(session, won.subject_id)
    gain = periods.HOST_POINTS
    return i18n.t("champion.win.host", lang, host=host, gain=gain, unit=_points(gain, lang))


def win_lines(session: Session, game_id: int, lang: str, now: datetime) -> list[str]:
    """The lines for a game that has just been won; [] for any other game,
    and for a win that fell in none of the periods running at `now`."""
    tz = settings.get_group_timezone(session)
    gains = periods.win_gains(session, game_id, now, tz)
    if not gains:
        return []
    winner = players.display_name(session, gains[0].player_id)
    lines = [gain_line(gains, winner, lang), _host_line(session, game_id, lang)]
    return [line for line in lines if line]
