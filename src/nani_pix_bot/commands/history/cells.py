"""The small markdown pieces /history's views share: escaped text, local
times, player names and table rows."""

from collections.abc import Iterable
from datetime import datetime
from zoneinfo import ZoneInfo

from sqlalchemy.orm import Session

from nani_pix_bot.commands.helpers.rich import md_escape
from nani_pix_bot.models.game import Game
from nani_pix_bot.services import i18n, players
from nani_pix_bot.services.achievements import periods

SHORT_TIME = "%d.%m %H:%M"


def t(key: str, lang: str, **kwargs: object) -> str:
    return md_escape(i18n.t(key, lang, **kwargs))


def as_utc(at: datetime) -> datetime:
    # DATETIME columns come back naive (UTC) from the DB.
    return at.replace(tzinfo=ZoneInfo("UTC")) if at.tzinfo is None else at


def local(at: datetime, tz: ZoneInfo, fmt: str) -> str:
    return as_utc(at).astimezone(tz).strftime(fmt)


def name(session: Session, player_id: int | None) -> str:
    return "—" if player_id is None else players.display_name(session, player_id)


def stage_total(game: Game) -> int:
    return len(periods.HARD_POINTS) if game.hard_mode else len(periods.WIN_POINTS)


def header(*cells: str) -> list[str]:
    """A table's header row (cells already escaped) and its rule."""
    return ["| " + " | ".join(cells) + " |", "|" + "---|" * len(cells)]


def row(cells: Iterable[str]) -> str:
    """A table row of raw text, escaped here."""
    return "| " + " | ".join(md_escape(c) for c in cells) + " |"
