"""How an achievement reads to people: localized name (with its tier),
description (with its threshold), and the title it confers."""

from nani_pix_bot.seasons import registry
from nani_pix_bot.seasons.definition import RunAchievement
from nani_pix_bot.services import i18n
from nani_pix_bot.services.achievements.definitions import Definition, Kind, threshold

_ROMAN = (
    (100, "C"),
    (90, "XC"),
    (50, "L"),
    (40, "XL"),
    (10, "X"),
    (9, "IX"),
    (5, "V"),
    (4, "IV"),
    (1, "I"),
)


def roman(n: int) -> str:
    out = []
    for value, numeral in _ROMAN:
        count, n = divmod(n, value)
        out.append(numeral * count)
    return "".join(out)


def _tiered(defn: Definition) -> bool:
    return len(defn.tiers) > 1 or defn.endless_step is not None


def _run_own(defn: Definition) -> RunAchievement | None:
    """The run module's own entry behind a run definition, if its run still loads."""
    run = registry.get(defn.run_id) if defn.run_id is not None else None
    if run is None:
        return None
    return next((a for a in run.achievements if f"{run.run_id}.{a.key}" == defn.key), None)


def _run_name(defn: Definition, lang: str) -> str | None:
    """A run achievement's own name (EN fallback), its key if the run is gone,
    or None for a definition that isn't a run's."""
    if defn.run_id is None:
        return None
    own = _run_own(defn)
    return own.name(lang) if own is not None else defn.key


def _run_description(defn: Definition, lang: str) -> str | None:
    """Like _run_name, for the description."""
    if defn.run_id is None:
        return None
    own = _run_own(defn)
    return own.description(lang) if own is not None else defn.key


def name(defn: Definition, tier: int, lang: str) -> str:
    base = _run_name(defn, lang) or i18n.t(f"achievement.{defn.key}.name", lang)
    return f"{base} {roman(tier)}" if tier > 0 and _tiered(defn) else base


def description(defn: Definition, tier: int, lang: str) -> str:
    own = _run_description(defn, lang)
    if own is not None:
        return own
    return i18n.t(f"achievement.{defn.key}.desc", lang, n=threshold(defn, max(tier, 1)))


def period_label(period_key: str, lang: str) -> str:
    """'2026-W41' -> '41 (2026)', '2026-10' -> 'October 2026', '2026' as is."""
    if "-W" in period_key:
        year, week = period_key.split("-W")
        return i18n.t("achievement.period.week", lang, week=int(week), year=year)
    if "-" in period_key:
        year, month = period_key.split("-")
        month_name = i18n.t(f"achievement.month.{int(month)}", lang)
        return i18n.t("achievement.period.month", lang, month=month_name, year=year)
    return period_key


def season_label(period_key: str, lang: str) -> str:
    """A season achievement's period key is its run id: the run's name, else the id."""
    run = registry.get(period_key)
    return run.name(lang) if run is not None else period_key


def title(defn: Definition, tier: int, period_key: str, lang: str) -> str:
    if defn.kind is Kind.PERIOD and period_key:
        label = period_label(period_key, lang)
        return i18n.t(f"achievement.{defn.key}.title", lang, period=label)
    if defn.kind is Kind.SEASON and defn.titled and period_key:
        label = season_label(period_key, lang)
        return i18n.t(f"achievement.{defn.key}.title", lang, season=label)
    return name(defn, tier, lang)
