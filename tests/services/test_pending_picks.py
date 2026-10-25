from sqlalchemy.orm import Session

from nani_pix_bot.models.pending_pick import PendingPick
from nani_pix_bot.services import pending_picks

ITEM = "sakura_1/profile_banner.blossom_avenue"


def test_a_kept_item_id_reads_back_by_its_row_id(session: Session) -> None:
    first = pending_picks.keep(session, ITEM)
    second = pending_picks.keep(session, "demo_1/name_flair.blossom")

    assert first != second
    assert pending_picks.item_id(session, first) == ITEM
    assert pending_picks.item_id(session, second) == "demo_1/name_flair.blossom"


def test_a_forgotten_or_unknown_row_reads_back_as_none(session: Session) -> None:
    row_id = pending_picks.keep(session, ITEM)

    pending_picks.forget(session, row_id)
    pending_picks.forget(session, row_id)  # already gone: no error

    assert pending_picks.item_id(session, row_id) is None
    assert pending_picks.item_id(session, 12345) is None
    assert session.query(PendingPick).count() == 0
