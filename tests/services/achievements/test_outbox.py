import pytest
from sqlalchemy.orm import Session

from nani_pix_bot.models.achievement import AchievementGrant
from nani_pix_bot.models.enums import OutboxKind
from nani_pix_bot.models.player import Player
from nani_pix_bot.services.achievements import engine, outbox

pytestmark = pytest.mark.achievements


def test_a_grant_queues_its_unlock(session: Session) -> None:
    session.add(Player(telegram_user_id=1))
    session.flush()

    row = engine.grant(session, engine.GrantRequest(1, "kingmaker"), batch_id=77)

    (queued,) = outbox.pending(session, 10)
    assert queued.kind == OutboxKind.UNLOCK
    assert queued.payload == {"grant_id": row.id}
    assert queued.batch_id == 77


def test_a_cascaded_unlock_is_queued_after_its_cause(session: Session) -> None:
    session.add(Player(telegram_user_id=1))
    session.flush()

    engine.grant(session, engine.GrantRequest(1, "pioneer"))  # 500 💠 -> Pixel Magnate I

    grants = [
        session.get(AchievementGrant, r.payload["grant_id"]) for r in outbox.pending(session, 10)
    ]
    keys = [g.key for g in grants if g is not None]
    assert keys == ["pioneer", "pixel_magnate"]


def test_posted_and_given_up_rows_leave_pending(session: Session) -> None:
    first = outbox.enqueue_unlock(session, 1, None)
    second = outbox.enqueue_unlock(session, 2, None)

    outbox.mark_posted(session, [first.id])
    for _ in range(outbox.MAX_ATTEMPTS):
        outbox.mark_failed(session, [second.id])

    assert outbox.pending(session, 10) == []
