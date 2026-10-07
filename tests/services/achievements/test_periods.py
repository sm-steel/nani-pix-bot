from datetime import UTC, datetime
from typing import Any
from zoneinfo import ZoneInfo

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from nani_pix_bot.models.achievement import AchievementGrant
from nani_pix_bot.models.enums import EventType, OutboxKind, PeriodType
from nani_pix_bot.models.period import PeriodResult
from nani_pix_bot.models.player import Player
from nani_pix_bot.services import events
from nani_pix_bot.services.achievements import outbox, periods
from tests.services.achievements.fake_history import ev

MSK = ZoneInfo("Europe/Moscow")
BERLIN = ZoneInfo("Europe/Berlin")
A, B, HOST = 1, 2, 9


def test_week_starts_on_local_monday_midnight() -> None:
    # Sunday 23:30 Moscow is still last week; Monday 00:30 is the new one.
    sunday = datetime(2026, 10, 11, 20, 30, tzinfo=UTC)  # 23:30 MSK
    monday = datetime(2026, 10, 11, 21, 30, tzinfo=UTC)  # 00:30 MSK
    assert periods.period_at(PeriodType.WEEK, sunday, MSK).key == "2026-W41"
    week = periods.period_at(PeriodType.WEEK, monday, MSK)
    assert week.key == "2026-W42"
    assert week.start == datetime(2026, 10, 11, 21, tzinfo=UTC)


def test_month_and_year_roll_over() -> None:
    at = datetime(2026, 12, 31, 22, tzinfo=UTC)  # Jan 1 01:00 MSK
    assert periods.period_at(PeriodType.MONTH, at, MSK).key == "2027-01"
    assert periods.period_at(PeriodType.YEAR, at, MSK).key == "2027"
    december = periods.period_at(PeriodType.MONTH, datetime(2026, 12, 15, tzinfo=UTC), MSK)
    assert periods.following(december, MSK).key == "2027-01"


def test_a_dst_week_spans_its_real_local_midnights() -> None:
    # Berlin leaves DST on 2026-10-25: Monday 19 Oct starts at 22:00Z (CEST),
    # Monday 26 Oct at 23:00Z (CET).
    week = periods.period_at(PeriodType.WEEK, datetime(2026, 10, 22, tzinfo=UTC), BERLIN)
    assert week.start == datetime(2026, 10, 18, 22, tzinfo=UTC)
    assert week.end == datetime(2026, 10, 25, 23, tzinfo=UTC)


def _won(winner: int, game: int, ended: datetime, **data: Any):
    facts = {"stage": 1, "hard_mode": False, "ended_at": ended.isoformat()} | data
    return ev(EventType.GAME_WON, winner, HOST, game=game, **facts)


OCT = periods.period_at(PeriodType.MONTH, datetime(2026, 10, 15, tzinfo=UTC), ZoneInfo("UTC"))


def test_score_weights_stages_and_hard_mode_and_credits_the_host() -> None:
    standings = periods.score(
        [
            _won(A, 1, datetime(2026, 10, 2, tzinfo=UTC), stage=1),  # 5
            _won(A, 2, datetime(2026, 10, 3, tzinfo=UTC), stage=5),  # 1
            _won(B, 3, datetime(2026, 10, 4, tzinfo=UTC), stage=1, hard_mode=True),  # 6, no host
        ],
        OCT,
    )
    assert [(s.player_id, s.score, s.wins) for s in standings] == [
        (A, 6, 2),
        (B, 6, 1),
        (HOST, 2, 0),
    ]


def test_ties_go_to_more_wins_then_whoever_got_there_first() -> None:
    standings = periods.score(
        [
            _won(A, 1, datetime(2026, 10, 5, tzinfo=UTC), stage=3),
            _won(B, 2, datetime(2026, 10, 2, tzinfo=UTC), stage=3),
        ],
        OCT,
    )
    assert [s.player_id for s in standings[:2]] == [B, A]


def test_games_ending_outside_the_period_dont_count_and_a_refinish_counts_once() -> None:
    standings = periods.score(
        [
            _won(A, 1, datetime(2026, 9, 30, tzinfo=UTC)),
            _won(B, 2, datetime(2026, 10, 2, tzinfo=UTC)),
            _won(B, 2, datetime(2026, 10, 2, tzinfo=UTC), how="setwinner"),
        ],
        OCT,
    )
    assert [(s.player_id, s.wins) for s in standings if s.player_id != HOST] == [(B, 1)]


@pytest.mark.achievements
def test_finalize_records_the_top_and_grants_the_champion_after_the_summary(
    session: Session,
) -> None:
    session.add_all([Player(telegram_user_id=u) for u in (A, B, HOST)])
    session.flush()
    ended = datetime(2026, 10, 2, tzinfo=UTC).isoformat()
    for game, winner in ((1, A), (2, A), (3, B)):
        events.emit(
            session,
            EventType.GAME_WON,
            events.Involved(actor_id=winner, subject_id=HOST, game_id=game),
            stage=1,
            hard_mode=False,
            how="guess",
            pot=0,
            seconds=None,
            last_slot=False,
            distinct_guessers=1,
            winner_wrong=0,
            first_guess=False,
            ended_at=ended,
        )

    top = periods.finalize(session, OCT)

    assert [s.player_id for s in top] == [A, B, HOST]
    ranks = session.scalars(select(PeriodResult.player_id).order_by(PeriodResult.rank)).all()
    assert ranks == [A, B, HOST]
    champion = session.scalars(
        select(AchievementGrant).where(AchievementGrant.key == "champion_month")
    ).one()
    assert (champion.player_id, champion.period_key) == (A, "2026-10")
    queued = [r for r in outbox.pending(session, 100) if r.kind == OutboxKind.PERIOD_SUMMARY]
    assert queued[0].payload == {"period_type": "month", "period_key": "2026-10"}
    champion_row = next(
        r for r in outbox.pending(session, 100) if r.payload.get("grant_id") == champion.id
    )
    assert champion_row.id > queued[0].id


@pytest.mark.achievements
def test_finalizing_an_empty_period_records_and_grants_nothing(session: Session) -> None:
    assert periods.finalize(session, OCT) == []
    assert outbox.pending(session, 10) == []


def test_rank_of_follows_the_standings_and_champion_keys_map_back(session: Session) -> None:
    period = periods.period_at(PeriodType.WEEK, datetime(2026, 10, 7, tzinfo=UTC), ZoneInfo("UTC"))
    assert periods.rank_of(session, period, 1) is None
    assert periods.PERIOD_OF_CHAMPION["champion_month"] is PeriodType.MONTH
