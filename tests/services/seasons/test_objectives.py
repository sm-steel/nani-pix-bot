from datetime import UTC, datetime, timedelta

from sqlalchemy import func, select

from nani_pix_bot.models.announcement import AnnouncementOutbox
from nani_pix_bot.models.enums import CurrencyReason, EventType, OutboxKind, XpSource
from nani_pix_bot.models.season import SeasonObjective, SeasonXp
from nani_pix_bot.seasons import registry
from nani_pix_bot.services import events
from nani_pix_bot.services.economy import wallet
from nani_pix_bot.services.events import Involved
from nani_pix_bot.services.seasons import objectives
from tests.seasons_fixtures import STARTER, WINNER


def _done(session) -> list[tuple[int, str]]:
    rows = session.scalars(select(SeasonObjective).order_by(SeasonObjective.objective_key))
    return [(r.player_id, r.objective_key) for r in rows]


def _tip(session, *, actor: int | None = WINNER) -> None:
    events.emit(
        session,
        EventType.CURRENCY_MOVED,
        Involved(actor_id=actor, subject_id=STARTER),
        amount=1,
        reason=CurrencyReason.TIP.value,
        currency="base",
        season_id=None,
        from_type="player",
        to_type="player",
        reversal=False,
    )


def test_an_early_win_completes_first_bloom(session, tagged_game, active_season, emit_win) -> None:
    emit_win(winner=WINNER, stage=2)
    assert (WINNER, "first_bloom") in _done(session)
    objective_xp = session.scalars(
        select(SeasonXp.amount).where(SeasonXp.source == XpSource.OBJECTIVE)
    ).all()
    assert objective_xp == [60]
    # 25 (win) + 40 (objective) + 60 (pass tier 2 free reward: 40 + 60 XP = 100 ≥ 60)
    assert wallet.balance(session, WINNER, season_id=active_season.id) == 25 + 40 + 60


def test_a_late_win_does_not(session, tagged_game, emit_win) -> None:
    emit_win(winner=WINNER, stage=4)
    assert (WINNER, "first_bloom") not in _done(session)


def test_an_objective_pays_once(session, tagged_game, active_season, emit_win) -> None:
    emit_win(winner=WINNER, stage=1)
    emit_win(winner=WINNER, stage=1)
    count = select(func.count()).select_from(SeasonXp).where(SeasonXp.source == XpSource.OBJECTIVE)
    assert session.scalar(count) == 1


def test_wins_in_untagged_games_do_not_count(
    session, untagged_game, active_season, emit_win
) -> None:
    emit_win(winner=WINNER, stage=1)
    assert _done(session) == []


def _running_now(session, season) -> None:
    """A game-less event is stamped with the real clock, so the season's
    dates must cover it."""
    now = datetime.now(UTC)
    season.start_at, season.end_at = now - timedelta(days=1), now + timedelta(days=9)
    session.flush()


def test_a_tip_during_the_season_counts(session, active_season, tagged_game) -> None:
    _running_now(session, active_season)
    _tip(session)
    assert (WINNER, "senpai_notice") in _done(session)


def test_a_tip_outside_a_season_does_not(session, untagged_game) -> None:
    _tip(session)
    assert _done(session) == []


def test_a_tip_before_the_season_does_not(session, active_season, tagged_game) -> None:
    active_season.start_at = datetime.now(UTC) + timedelta(days=1)
    session.flush()
    _tip(session)
    assert _done(session) == []


def test_the_house_completes_nothing(session, active_season, tagged_game) -> None:
    _running_now(session, active_season)
    _tip(session, actor=None)
    assert _done(session) == []


def test_no_objectives_once_the_season_ended(session, tagged_game, end_season, emit_win) -> None:
    end_season()
    emit_win(winner=WINNER, stage=1)
    assert _done(session) == []


def test_completion_sends_a_notice_and_syncs_the_pass(
    session, tagged_game, active_season, emit_win
) -> None:
    emit_win(winner=WINNER, stage=1)
    kinds = session.scalars(select(AnnouncementOutbox.kind)).all()
    assert OutboxKind.SEASON_OBJECTIVE in kinds
    # win at stage 1 = 50 XP, + 60 objective XP = 110 → tiers 1 and 2 of demo_1 (20, 60)
    tiers = session.scalars(
        select(AnnouncementOutbox.payload).where(AnnouncementOutbox.kind == OutboxKind.SEASON_TIER)
    ).all()
    assert [g["tier"] for payload in tiers for g in payload["granted"]] == [1, 2]


def test_completion_is_logged(session, tagged_game, active_season, emit_win, log_records) -> None:
    emit_win(winner=WINNER, stage=1)
    record = next(r for r in log_records if "season objective" in r.message)
    assert record.level == "INFO"
    assert record.extra["objective"] == "first_bloom"
    assert record.extra["season_id"] == active_season.id
    assert record.extra["run_id"] == "demo_1"
    assert record.extra["player_id"] == WINNER


def test_status_caps_progress_at_the_target(session, active_season, tagged_game, emit_win) -> None:
    emit_win(winner=WINNER, stage=1)
    emit_win(winner=WINNER, stage=1)
    run = registry.get("demo_1")
    assert run is not None
    rows = {
        row.objective.key: row for row in objectives.status(session, active_season, run, WINNER)
    }
    assert (rows["first_bloom"].progress, rows["first_bloom"].done) == (1, True)
    assert (rows["hanami_host"].progress, rows["hanami_host"].done) == (0, False)


def test_status_reads_when_an_objective_was_completed(
    session, active_season, tagged_game, emit_win
) -> None:
    emit_win(winner=WINNER, stage=1)
    row = session.get(SeasonObjective, (active_season.id, WINNER, "first_bloom"))
    row.completed_at = datetime(2026, 11, 2, 21, 30, tzinfo=UTC).replace(tzinfo=None)
    session.flush()
    run = registry.get("demo_1")
    assert run is not None
    rows = {
        row.objective.key: row for row in objectives.status(session, active_season, run, WINNER)
    }
    assert rows["first_bloom"].completed_at == datetime(2026, 11, 2, 21, 30, tzinfo=UTC)
    assert rows["hanami_host"].completed_at is None
