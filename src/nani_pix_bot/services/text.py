"""Plain-string helpers shared by more than one message builder."""

import re
from datetime import timedelta

from nani_pix_bot.services import i18n

# ASCII punctuation rich-message markdown can give meaning to; a backslash
# makes each literal.
MD_SPECIAL = re.compile(r"([\\`*_~|=\[\](){}#>!+\-.])")


def md_escape(text: str) -> str:
    """For anything that isn't ours to format in a rich message: player
    names, titles, input."""
    return MD_SPECIAL.sub(r"\\\1", text)


def cut_at_line(text: str, limit: int, marker: str) -> str:
    """`text` if it fits in `limit` characters; otherwise as many whole
    lines as fit together with `marker` (a single over-long line is cut
    mid-line), then `marker`."""
    if len(text) <= limit:
        return text
    cut = text[: limit - len(marker)]
    last_newline = cut.rfind("\n")
    if last_newline > 0:
        cut = cut[:last_newline]
    return cut + marker


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
