from datetime import UTC, datetime, timedelta

import pytest

from nani_pix_bot.jobs import announcements
from nani_pix_bot.models.announcement import AnnouncementOutbox
from nani_pix_bot.models.enums import CurrencyReason, OutboxKind, SeasonStatus
from nani_pix_bot.models.player import Player
from nani_pix_bot.models.season import SeasonResult, SeasonSchedule
from nani_pix_bot.seasons import registry
from nani_pix_bot.services.achievements import engine, outbox
from nani_pix_bot.services.cards import SeasonBanner, UnlockCard, backgrounds
from nani_pix_bot.services.economy import wallet
from nani_pix_bot.services.seasons import conversion, schedule


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
    assert "This season only romance anime can be played." in post.text


def test_end_post_lists_the_podium(session) -> None:
    row = _row(session, OutboxKind.SEASON_END)
    session.add(Player(telegram_user_id=2, username="alice"))
    session.add(SeasonResult(season_id=row.payload["season_id"], player_id=2, rank=1, xp=120))
    session.flush()
    post = announcements.RENDERERS[OutboxKind.SEASON_END](session, row, "EN")
    assert post is not None
    assert "@alice" in post.text
    assert "120" in post.text


def test_the_banner_carries_the_runs_stamp_art(session, monkeypatch, tmp_path) -> None:
    folder = tmp_path / "demo_1"
    folder.mkdir()
    (folder / "stamp.png").write_bytes(b"plain stamp")
    (folder / "stamp_plus.png").write_bytes(b"plus stamp")
    monkeypatch.setattr(backgrounds, "SEASON_ART_DIR", tmp_path)
    post = _render(session, OutboxKind.SEASON_START)
    assert isinstance(post.card, SeasonBanner)
    assert post.card.stamp == b"plain stamp"


def test_a_run_without_stamp_art_gets_a_banner_without_a_stamp(session) -> None:
    post = _render(session, OutboxKind.SEASON_TEASER)
    assert isinstance(post.card, SeasonBanner)
    assert post.card.stamp is None


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


def test_end_post_keeps_everyone_tied_for_third(session) -> None:
    row = _row(session, OutboxKind.SEASON_END)
    season_id = row.payload["season_id"]
    standings = [(1, 1, 100), (2, 2, 80), (3, 3, 60), (4, 3, 60), (5, 5, 10)]
    for user, _rank, _xp in standings:
        session.add(Player(telegram_user_id=user, username=f"user{user}"))
    session.add_all(
        SeasonResult(season_id=season_id, player_id=user, rank=rank, xp=xp)
        for user, rank, xp in standings
    )
    session.flush()
    post = announcements.RENDERERS[OutboxKind.SEASON_END](session, row, "EN")
    assert post is not None
    for user in (1, 2, 3, 4):
        assert f"@user{user}" in post.text
    assert "@user5" not in post.text


@pytest.mark.parametrize(("lang", "line"), [("EN", "Nobody earned"), ("RU", "никто не заработал")])
def test_end_post_says_when_nobody_scored(session, lang, line) -> None:
    row = _row(session, OutboxKind.SEASON_END)
    post = announcements.RENDERERS[OutboxKind.SEASON_END](session, row, lang)
    assert post is not None
    assert line in post.text


def test_end_post_reports_the_coin_conversion(session) -> None:
    row = _row(session, OutboxKind.SEASON_END)
    season_id = row.payload["season_id"]
    player = Player(telegram_user_id=2, username="alice")
    session.add(player)
    session.flush()
    entry = wallet.LedgerEntry(CurrencyReason.GRANT, season_id=season_id, coins=True)
    wallet.credit(session, player, 9, entry)
    season = session.get(SeasonSchedule, season_id)
    conversion.convert(session, season, registry.all_runs()["demo_1"])
    post = announcements.RENDERERS[OutboxKind.SEASON_END](session, row, "EN")
    assert post is not None
    assert "4 💠" in post.text
    assert "2 🪙 = 1 💠" in post.text


def _unlock_row(session, key: str) -> AnnouncementOutbox:
    session.add(Player(telegram_user_id=1, username="alice"))
    session.flush()
    granted = engine.grant(session, engine.GrantRequest(1, key, 1, "demo_1"))
    return AnnouncementOutbox(kind=OutboxKind.UNLOCK, payload={"grant_id": granted.id})


@pytest.mark.parametrize(
    ("key", "shown"),
    [("season_debut", "Season Debut"), ("demo_1.double_date", "Double Date")],
)
def test_an_untitled_season_unlock_posts_its_name_not_a_raw_key(
    session, records, key: str, shown: str
) -> None:
    post = announcements._unlock_post(session, _unlock_row(session, key), "EN")

    assert post is not None
    assert shown in post.text
    assert "achievement." not in post.text
    assert isinstance(post.card, UnlockCard)
    assert post.card.name == shown
    assert not [message for level, message in records if level == "ERROR"]
