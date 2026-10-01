from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy.orm import Session

from nani_pix_bot.services import quiet_hours, settings
from nani_pix_bot.services.game import clock
from nani_pix_bot.services.quiet_hours import QuietHours


def test_deadline_after_without_quiet_hours_is_now_plus_delay(session: Session) -> None:
    before = datetime.now(UTC)
    deadline = clock.deadline_after(session, timedelta(hours=6))
    assert (deadline - before).total_seconds() == pytest.approx(6 * 3600, abs=5)


def test_deadline_after_skips_the_current_quiet_window(
    session: Session, quiet_now: QuietHours
) -> None:
    settings.set_quiet_hours(session, quiet_now)
    session.commit()
    before = datetime.now(UTC)
    deadline = clock.deadline_after(session, timedelta(hours=3))
    expected = quiet_hours.window_end_after(quiet_now, before) + timedelta(hours=3)
    assert abs((deadline - expected).total_seconds()) < 5
    assert not quiet_hours.is_quiet(quiet_now, deadline)
