from datetime import UTC, datetime

import pytest
from sqlalchemy.orm import Session

from nani_pix_bot.commands.achievements import render
from nani_pix_bot.commands.helpers.rich import md_escape
from nani_pix_bot.models.player import Player
from nani_pix_bot.services.achievements import catalogue, engine, status
from nani_pix_bot.services.achievements.status import State, Status

pytestmark = pytest.mark.achievements


def _titled(session: Session) -> None:
    session.add(Player(telegram_user_id=1, username="a_b", title_key="champion_month:1:2026-10"))
    session.flush()
    engine.grant(session, engine.GrantRequest(1, "champion_month", 1, "2026-10"))


def test_the_summary_shows_the_chosen_title(session: Session) -> None:
    _titled(session)

    text = render.summary(session, 1, "en", datetime.now(UTC))

    assert "Title: Champion of October 2026" in text


def test_the_top_table_shows_an_escaped_title_next_to_the_name(session: Session) -> None:
    _titled(session)

    text = render.top_table(session, status.top(session, limit=10), "en", 0)

    assert chr(92).join(["a", "_b «Champion of October 2026»"]) in text


def test_an_earned_champion_row_shows_the_current_race_rank() -> None:
    week = catalogue.get("champion_week")
    racing = Status(week, State.EARNED, tier=1, period_key="2026-W40", rank=2)

    line = render.row(racing, "en", None)

    assert " · " + md_escape("now #2") + " —" in line  # after the times-won count
    assert md_escape("сейчас #2") in render.row(racing, "ru", None)


def test_an_earned_champion_out_of_the_race_shows_no_rank() -> None:
    week = catalogue.get("champion_week")
    resting = Status(week, State.EARNED, tier=1, period_key="2026-W40")

    assert "now" not in render.row(resting, "en", None)
