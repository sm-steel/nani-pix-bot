"""The hex notices' posts: a Kappa or Kitsune going off, and an omikuji
self-prank. Text only, in the game topic."""

from collections.abc import Callable

from sqlalchemy import select
from sqlalchemy.orm import Session

from nani_pix_bot.jobs.post import Post
from nani_pix_bot.models.announcement import AnnouncementOutbox
from nani_pix_bot.models.enums import EventType, OutboxKind
from nani_pix_bot.models.event_log import EventLog
from nani_pix_bot.models.halloween import Hex
from nani_pix_bot.services import i18n, players


def _amount(session: Session, row: Hex) -> int:
    """What the hex took, read back from its HEX_TRIGGERED event."""
    stmt = select(EventLog).where(
        EventLog.event_type == EventType.HEX_TRIGGERED,
        EventLog.actor_id == row.target_id,
        EventLog.game_id == row.game_id,
    )
    for event in session.scalars(stmt.order_by(EventLog.id.desc())):
        if event.data.get("hex_id") == row.id:
            return int(event.data.get("amount", 0))
    return 0


def _triggered(session: Session, row: Hex, lang: str) -> str:
    return i18n.t(
        f"hex.notice.triggered.{row.kind}",
        lang,
        target=players.display_name(session, row.target_id),
        caster=players.display_name(session, row.caster_id),
        amount=_amount(session, row),
    )


def _self_prank(session: Session, row: Hex, lang: str) -> str:
    hours = 0
    if row.expires_at is not None:
        hours = round((row.expires_at - row.cast_at).total_seconds() / 3600)
    return i18n.t(
        f"hex.notice.self_prank.{row.kind}",
        lang,
        target=players.display_name(session, row.target_id),
        hours=hours,
    )


def _hex_post(session: Session, row: AnnouncementOutbox, lang: str) -> Post | None:
    hexed = session.get(Hex, row.payload["hex_id"])
    if hexed is None:
        return None
    render = _self_prank if row.payload["notice"] == "self_prank" else _triggered
    return Post((row.id,), render(session, hexed, lang))


RENDERERS: dict[OutboxKind, Callable[[Session, AnnouncementOutbox, str], Post | None]] = {
    OutboxKind.HEX_NOTICE: _hex_post,
}
