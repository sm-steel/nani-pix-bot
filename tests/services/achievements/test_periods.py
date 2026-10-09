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
    """A win with one wrong guess of the winner's own (no clean bonus)
    unless the test says otherwise."""
    facts = {"stage": 1, "hard_mode": False, "winner_wrong": 1, "ended_at": ended.isoformat()}
    facts |= data
    return ev(EventType.GAME_WON, winner, HOST, game=game, **facts)


OCT = periods.period_at(PeriodType.MONTH, datetime(2026, 10, 15, tzinfo=UTC), ZoneInfo("UTC"))
OCT_2 = datetime(2026, 10, 2, tzinfo=UTC)


def test_a_win_with_no_wrong_guesses_of_your_own_earns_the_clean_bonus() -> None:
    clean = _won(A, 1, OCT_2, stage=2, winner_wrong=0)
    assert periods.is_clean(clean)
    assert periods.win_points(clean) == periods.WIN_POINTS[1] + periods.CLEAN_BONUS


def test_a_wrong_guess_of_your_own_loses_the_clean_bonus() -> None:
    won = _won(A, 1, OCT_2, stage=2, winner_wrong=1)
    assert not periods.is_clean(won)
    assert periods.win_points(won) == periods.WIN_POINTS[1]


def test_hard_mode_never_gets_the_clean_bonus() -> None:
    won = _won(A, 1, OCT_2, stage=1, hard_mode=True, winner_wrong=0)
    assert not periods.is_clean(won)
    assert periods.win_points(won) == periods.HARD_POINTS[0]


@pytest.mark.parametrize("how", ["correct", "vote", "setwinner"])
def test_a_win_the_matcher_missed_doesnt_count_the_winning_guess_as_wrong(how: str) -> None:
    # /correct, a vote and /setwinner all store the winning guess as wrong.
    won = _won(A, 1, OCT_2, how=how, winner_wrong=1)
    assert periods.wrong_before_win(won) == 0
    assert periods.is_clean(won)
    assert periods.wrong_before_win(_won(A, 2, OCT_2, how=how, winner_wrong=3)) == 2
    assert periods.wrong_before_win(_won(A, 3, OCT_2, how=how, winner_wrong=0)) == 0


def test_a_matched_guess_counts_every_wrong_guess() -> None:
    assert periods.wrong_before_win(_won(A, 1, OCT_2, how="guess", winner_wrong=2)) == 2


def test_an_unknown_stage_earns_nothing_not_even_the_clean_bonus() -> None:
    assert periods.win_points(_won(A, 1, OCT_2, stage=0, winner_wrong=0)) == 0


def test_an_unknown_stage_is_never_marked_clean() -> None:
    won = _won(A, 1, OCT_2, stage=0, winner_wrong=0)
    assert not periods.is_clean(won)
    win = next(g for g in periods.gains([won], OCT) if g.role is periods.GainRole.WIN)
    assert (win.points, win.clean) == (0, False)


def test_an_old_win_without_the_wrong_count_counts_as_clean() -> None:
    old = ev(EventType.GAME_WON, A, HOST, game=1, stage=1, hard_mode=False, ended_at="x")
    assert periods.wrong_before_win(old) == 0
    assert periods.is_clean(old)


def test_gains_mark_a_clean_win_but_never_the_host_share() -> None:
    gains = periods.gains([_won(A, 1, OCT_2, stage=1, winner_wrong=0)], OCT)
    assert [(g.role, g.points, g.clean) for g in gains] == [
        (periods.GainRole.WIN, periods.WIN_POINTS[0] + periods.CLEAN_BONUS, True),
        (periods.GainRole.HOST, periods.HOST_POINTS, False),
    ]


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


def _order(won: list[Any]) -> list[int]:
    return [s.player_id for s in periods.score(won, OCT) if s.player_id != HOST]


def test_ties_go_to_more_wins() -> None:
    won = [
        _won(A, 1, OCT_2, stage=1),  # 5 from one win
        _won(B, 2, OCT_2, stage=3),
        _won(B, 3, OCT_2, stage=4),  # 3 + 2 from two
    ]
    assert _order(won) == [B, A]


def test_then_fewer_wrong_guesses_in_the_won_games() -> None:
    won = [
        _won(A, 1, datetime(2026, 10, 2, tzinfo=UTC), winner_wrong=3),
        _won(B, 2, datetime(2026, 10, 5, tzinfo=UTC), winner_wrong=1),
    ]
    assert _order(won) == [B, A]
    board = {s.player_id: s for s in periods.score(won, OCT)}
    assert (board[A].wrong, board[B].wrong, board[HOST].wrong) == (3, 1, 0)


def test_then_less_total_solve_time_with_unknown_time_as_zero() -> None:
    won = [_won(A, 1, OCT_2, seconds=300.0), _won(B, 2, OCT_2, seconds=120.0)]
    assert _order(won) == [B, A]
    board = {s.player_id: s for s in periods.score(won, OCT)}
    assert (board[A].seconds, board[B].seconds) == (300.0, 120.0)
    unknown = periods.score([_won(A, 1, OCT_2, seconds=None)], OCT)
    assert unknown[0].seconds == 0


def test_wrong_guesses_only_count_for_wins_inside_the_period() -> None:
    won = [
        _won(A, 1, datetime(2026, 9, 28, tzinfo=UTC), winner_wrong=5),  # September
        _won(A, 2, OCT_2, winner_wrong=2, seconds=10.0),
        _won(B, 3, OCT_2, winner_wrong=2, seconds=20.0),
    ]
    assert _order(won) == [A, B]
    assert periods.score(won, OCT)[0].wrong == 2


def test_players_tied_on_every_key_share_the_rank() -> None:
    won = [
        _won(A, 1, OCT_2, seconds=60.0),
        _won(B, 2, datetime(2026, 10, 9, tzinfo=UTC), seconds=60.0),
    ]
    board = periods.score(won, OCT)
    ranked = periods.ranked(board)
    assert [(rank, s.player_id) for rank, s in ranked] == [(1, A), (1, B), (3, HOST)]
    assert periods.rank_key(board[0]) == periods.rank_key(board[1])


def test_shared_ranks_skip_after_a_tie_and_resume_on_a_new_key() -> None:
    board = [
        periods.Standing(1, score=9),
        periods.Standing(2, score=5),
        periods.Standing(3, score=5),
        periods.Standing(4, score=5),
        periods.Standing(5, score=4),
        periods.Standing(6, score=4),
    ]
    assert [rank for rank, _ in periods.ranked(board)] == [1, 2, 2, 2, 5, 5]


def test_gains_report_shared_ranks() -> None:
    gains = periods.gains([_won(A, 1, OCT_2, seconds=60.0), _won(B, 2, OCT_2, seconds=60.0)], OCT)
    b_win = next(g for g in gains if g.player_id == B)
    assert (b_win.rank_before, b_win.rank_after) == (None, 1)


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


def _gain_rows(gains: list[periods.Gain]) -> list[tuple[Any, ...]]:
    return [(g.player_id, g.points, g.role, g.rank_before, g.rank_after) for g in gains]


def test_gains_replay_each_win_with_the_ranks_it_moved() -> None:
    gains = periods.gains(
        [
            _won(A, 1, datetime(2026, 10, 2, tzinfo=UTC), stage=4),  # A 2, host 1
            _won(B, 2, datetime(2026, 10, 3, tzinfo=UTC), stage=1),  # B 5, host 2
        ],
        OCT,
    )
    win, host = periods.GainRole.WIN, periods.GainRole.HOST
    assert _gain_rows(gains) == [
        (A, 2, win, None, 1),
        (HOST, 1, host, None, 2),
        (B, 5, win, None, 1),
        (HOST, 1, host, 2, 3),
    ]
    assert gains[2].game_id == 2
    assert gains[2].stage == 1
    assert gains[2].at == datetime(2026, 10, 3, tzinfo=UTC)


def test_gains_give_no_host_share_in_hard_mode_and_report_the_turn() -> None:
    gains = periods.gains(
        [_won(A, 1, datetime(2026, 10, 2, tzinfo=UTC), stage=2, hard_mode=True)], OCT
    )
    assert _gain_rows(gains) == [(A, 4, periods.GainRole.WIN, None, 1)]
    assert gains[0].hard_mode


def test_a_host_who_also_wins_gets_both_shares_like_the_standings() -> None:
    won = ev(
        EventType.GAME_WON,
        A,
        A,
        game=1,
        stage=1,
        hard_mode=False,
        winner_wrong=1,
        ended_at=datetime(2026, 10, 2, tzinfo=UTC).isoformat(),
    )
    gains = periods.gains([won], OCT)
    assert [(g.points, g.role) for g in gains] == [
        (5, periods.GainRole.WIN),
        (1, periods.GainRole.HOST),
    ]
    assert periods.score([won], OCT)[0].score == sum(g.points for g in gains)


def test_gains_skip_other_periods_and_count_a_refinish_once() -> None:
    gains = periods.gains(
        [
            _won(A, 1, datetime(2026, 9, 30, tzinfo=UTC)),
            _won(B, 2, datetime(2026, 10, 2, tzinfo=UTC)),
            _won(A, 2, datetime(2026, 10, 2, tzinfo=UTC), how="setwinner"),
        ],
        OCT,
    )
    assert [(g.player_id, g.role) for g in gains] == [
        (A, periods.GainRole.WIN),
        (HOST, periods.GainRole.HOST),
    ]


def _emit_wins(session: Session, *winners: int) -> None:
    """One stage-1 win per entry (games 1, 2, ...), all hosted by HOST."""
    session.add_all([Player(telegram_user_id=u) for u in {*winners, HOST}])
    session.flush()
    ended = datetime(2026, 10, 2, tzinfo=UTC).isoformat()
    for game, winner in enumerate(winners, start=1):
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
            winner_wrong=1,
            first_guess=False,
            ended_at=ended,
        )


def _champions(session: Session) -> list[int]:
    stmt = select(AchievementGrant.player_id).where(AchievementGrant.key == "champion_month")
    return sorted(session.scalars(stmt))


def _summaries(session: Session) -> list[Any]:
    return [r for r in outbox.pending(session, 100) if r.kind == OutboxKind.PERIOD_SUMMARY]


def _stored(session: Session) -> list[tuple[int, int]]:
    stmt = select(PeriodResult.rank, PeriodResult.player_id).order_by(
        PeriodResult.rank, PeriodResult.id
    )
    return [(rank, player) for rank, player in session.execute(stmt)]


@pytest.mark.achievements
def test_finalize_records_the_top_and_grants_the_champion_after_the_summary(
    session: Session,
) -> None:
    _emit_wins(session, A, A, B)

    top = periods.finalize(session, OCT)

    assert [(rank, s.player_id) for rank, s in top] == [(1, A), (2, B), (3, HOST)]
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
def test_everyone_tied_at_first_becomes_champion(session: Session, log_records) -> None:
    c = 3
    _emit_wins(session, A, B, c)  # 5 each; the host's 3 is fourth, off the podium

    top = periods.finalize(session, OCT)

    assert [rank for rank, _ in top] == [1, 1, 1]
    assert _stored(session) == [(1, A), (1, B), (1, c)]
    assert _champions(session) == [A, B, c]
    assert len(_summaries(session)) == 1
    closed = next(r for r in log_records if r.message.startswith("closed month"))
    assert closed.level == "INFO"
    assert "champions 1, 2, 3" in closed.message


@pytest.mark.achievements
def test_two_champions_store_two_first_places(session: Session) -> None:
    _emit_wins(session, A, B)

    periods.finalize(session, OCT)

    assert _stored(session) == [(1, A), (1, B), (3, HOST)]
    assert _champions(session) == [A, B]


@pytest.mark.achievements
def test_a_tie_on_the_podium_puts_everyone_tied_on_it(session: Session) -> None:
    c, d = 3, 4
    _emit_wins(session, A, A, B, c, d)  # A 10; B, c, d and the host 5, the host 0 👑

    top = periods.finalize(session, OCT)

    assert len(top) == periods.TOP_SIZE + 1
    assert _stored(session) == [(1, A), (2, B), (2, c), (2, d)]
    assert _champions(session) == [A]


@pytest.mark.achievements
def test_a_tie_too_big_at_first_crowns_nobody_but_still_posts(
    session: Session, log_records
) -> None:
    others = (3, 4)
    _emit_wins(session, A, B, *others)  # four players on 5, the host on 4

    top = periods.finalize(session, OCT)

    assert len(top) == periods.CHAMPION_CAP + 1
    assert _stored(session) == [(1, A), (1, B), (1, 3), (1, 4)]
    assert _champions(session) == []
    assert len(_summaries(session)) == 1
    messages = [r.message for r in log_records if r.level == "INFO"]
    assert "no champion for month 2026-10: 4-way tie at #1" in messages
    assert any(m.startswith("closed month 2026-10") and "nobody" in m for m in messages)


@pytest.mark.achievements
def test_finalizing_an_empty_period_records_and_grants_nothing(session: Session) -> None:
    assert periods.finalize(session, OCT) == []
    assert outbox.pending(session, 10) == []


def test_rank_of_follows_the_standings_and_champion_keys_map_back(session: Session) -> None:
    period = periods.period_at(PeriodType.WEEK, datetime(2026, 10, 7, tzinfo=UTC), ZoneInfo("UTC"))
    assert periods.rank_of(session, period, 1) is None
    assert periods.PERIOD_OF_CHAMPION["champion_month"] is PeriodType.MONTH
