"""Item ids too long for a player menu's callback data (issue #479): the
`/grantitem <item id>` menu keeps the id in a row and its buttons carry the
row's id. Telegram caps callback data at 64 bytes, and some item ids are
nearly 40 characters on their own."""

from sqlalchemy.orm import Session

from nani_pix_bot.models.pending_pick import PendingPick


def keep(session: Session, item_id: str) -> int:
    """Store `item_id`; the row id the menu's buttons carry."""
    row = PendingPick(item_id=item_id)
    session.add(row)
    session.flush()
    return row.id


def item_id(session: Session, row_id: int) -> str | None:
    """The item a menu's row holds; None once it is gone."""
    row = session.get(PendingPick, row_id)
    return None if row is None else row.item_id


def forget(session: Session, row_id: int) -> None:
    """Drop the row (the menu granted or was cancelled); a missing one is fine."""
    row = session.get(PendingPick, row_id)
    if row is not None:
        session.delete(row)
        session.flush()
