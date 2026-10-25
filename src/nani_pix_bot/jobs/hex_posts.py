"""The hex notices' posts: a Kappa or Kitsune going off. Text only, in the game
topic. (An omikuji self-prank is shown by the omikuji card from /omikuji instead.)"""

from collections.abc import Callable

from sqlalchemy import select
from sqlalchemy.orm import Session

from nani_pix_bot.jobs.post import Post
from nani_pix_bot.models.announcement import AnnouncementOutbox
from nani_pix_bot.models.enums import EventType, OutboxKind
from nani_pix_bot.models.event_log import EventLog
from nani_pix_bot.models.halloween import Hex
from nani_pix_bot.seasons.mechanics import HexKind
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
    amount = _amount(session, row)
    key = f"hex.notice.triggered.{row.kind}"
    if row.kind == HexKind.KITSUNE and amount == 0:
        key = "hex.notice.triggered.kitsune_empty"  # nothing in the purse to take
    return i18n.t(
        key,
        lang,
        target=players.display_name(session, row.target_id),
        caster=players.display_name(session, row.caster_id),
        amount=amount,
    )


def _hex_post(session: Session, row: AnnouncementOutbox, lang: str) -> Post | None:
    hexed = session.get(Hex, row.payload["hex_id"])
    if hexed is None:
        return None
    return Post((row.id,), _triggered(session, hexed, lang))


RENDERERS: dict[OutboxKind, Callable[[Session, AnnouncementOutbox, str], Post | None]] = {
    OutboxKind.HEX_NOTICE: _hex_post,
}
