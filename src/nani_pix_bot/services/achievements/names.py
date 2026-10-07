"""How an achievement reads to people: localized name (with its tier),
description (with its threshold), and the title it confers."""

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


def name(defn: Definition, tier: int, lang: str) -> str:
    base = i18n.t(f"achievement.{defn.key}.name", lang)
    return f"{base} {roman(tier)}" if tier > 0 and _tiered(defn) else base


def description(defn: Definition, tier: int, lang: str) -> str:
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


def title(defn: Definition, tier: int, period_key: str, lang: str) -> str:
    if defn.kind is Kind.PERIOD and period_key:
        label = period_label(period_key, lang)
        return i18n.t(f"achievement.{defn.key}.title", lang, period=label)
    return name(defn, tier, lang)
