from datetime import UTC, datetime, timedelta

import pytest

from nani_pix_bot.jobs import announcements
from nani_pix_bot.models.announcement import AnnouncementOutbox
from nani_pix_bot.models.enums import OutboxKind, SeasonStatus
from nani_pix_bot.models.player import Player
from nani_pix_bot.models.season import SeasonResult, SeasonSchedule
from nani_pix_bot.seasons import registry
from nani_pix_bot.services.achievements import outbox
from nani_pix_bot.services.cards import SeasonBanner
from nani_pix_bot.services.seasons import schedule


@pytest.fixture(autouse=True)
def fake_runs(monkeypatch):
    runs = registry.discover("tests.seasons.fake_runs")
    monkeypatch.setattr(registry, "all_runs", lambda: runs)


def _row(session, kind) -> AnnouncementOutbox:
    now = datetime(2026, 11, 1, 12, 0, tzinfo=UTC)
    season = SeasonSchedule(
        run_id="demo_1",
        start_at=now,
        end_at=now + timedelta(days=10),
        status=SeasonStatus.ENDED,
        created_by=9,
    )
    session.add(season)
    session.flush()
    row = AnnouncementOutbox(kind=kind, payload={"season_id": season.id})
    session.add(row)
    session.flush()
    return row


def _render(session, kind):
    return announcements.RENDERERS[kind](session, _row(session, kind), "EN")


def test_teaser_names_the_season_and_its_dates(session) -> None:
    post = _render(session, OutboxKind.SEASON_TEASER)
    assert "Demo Season" in post.text
    assert "01.11 12:00" in post.text  # group timezone defaults to UTC
    assert isinstance(post.card, SeasonBanner)


def test_start_post_states_the_gate_rule(session) -> None:
    post = _render(session, OutboxKind.SEASON_START)
    assert "Romance" in post.text


def test_end_post_lists_the_podium(session) -> None:
    row = _row(session, OutboxKind.SEASON_END)
    session.add(Player(telegram_user_id=2, username="alice"))
    session.add(SeasonResult(season_id=row.payload["season_id"], player_id=2, rank=1, xp=120))
    session.flush()
    post = announcements.RENDERERS[OutboxKind.SEASON_END](session, row, "EN")
    assert post is not None
    assert "@alice" in post.text
    assert "120" in post.text


def test_banner_draws_without_a_background_file(session) -> None:
    post = _render(session, OutboxKind.SEASON_TEASER)
    assert announcements._draw(post, [])[:8] == b"\x89PNG\r\n\x1a\n"


def test_a_cancelled_season_posts_nothing(session) -> None:
    now = datetime(2026, 11, 1, 12, 0, tzinfo=UTC)
    request = schedule.ScheduleRequest(
        run_id="demo_1",
        start_at=now + timedelta(days=1),
        end_at=now + timedelta(days=5),
        admin_id=7,
    )
    schedule.schedule(session, request, now)
    schedule.cancel(session, now)
    row = outbox.pending(session, 10)[0]
    assert announcements.RENDERERS[OutboxKind.SEASON_TEASER](session, row, "EN") is None
