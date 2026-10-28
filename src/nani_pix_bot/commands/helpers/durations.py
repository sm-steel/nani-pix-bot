"""Short human durations for player-facing text: '2d 3h', '1h 5m', '12m'
(`duration` lives in services.text so services can use it too)."""

from nani_pix_bot.services.text import duration as duration


def clock(seconds: float) -> str:
    """A total to the second, for tables where seconds can decide a tie:
    'm:ss' under an hour, 'h:mm:ss' from an hour up."""
    hours, rest = divmod(int(seconds), 3600)
    minutes, secs = divmod(rest, 60)
    return f"{hours}:{minutes:02d}:{secs:02d}" if hours else f"{minutes}:{secs:02d}"
