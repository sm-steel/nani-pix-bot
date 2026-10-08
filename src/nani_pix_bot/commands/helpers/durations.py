"""Short human durations for player-facing text: '2d 3h', '1h 5m', '12m'."""

from datetime import timedelta

from nani_pix_bot.services import i18n


def duration(span: timedelta, lang: str) -> str:
    """The two largest non-zero units, rounded down to the minute (at least 1m)."""
    minutes = max(int(span.total_seconds()) // 60, 1)
    days, rest = divmod(minutes, 24 * 60)
    hours, mins = divmod(rest, 60)
    parts = [
        i18n.t(f"duration.{unit}", lang, n=n)
        for unit, n in (("day", days), ("hour", hours), ("minute", mins))
        if n
    ]
    return " ".join(parts[:2])
