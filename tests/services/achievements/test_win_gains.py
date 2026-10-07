from datetime import UTC, datetime
from zoneinfo import ZoneInfo

from sqlalchemy.orm import Session

from nani_pix_bot.models.enums import EventType, PeriodType
from nani_pix_bot.models.player import Player
from nani_pix_bot.services import events
from nani_pix_bot.services.achievements import periods, win_lines
from nani_pix_bot.services.achievements.periods import PeriodGain

UTC_TZ = ZoneInfo("UTC")
NOW = datetime(2026, 10, 7, 12, tzinfo=UTC)
A, B, W = 1, 2, 3
OCT_3 = datetime(2026, 10, 3, 12, tzinfo=UTC)  # a Saturday: last week, this month


def _win(
    session: Session,
    game: int,
    winner: int,
    *,
    host: int | None = None,
    stage: int = 1,
    hard_mode: bool = False,
    ended: datetime = NOW,
) -> None:
    host = host if host is not None else 100 + game  # a fresh host per game
    for pid in (winner, host):
        if session.get(Player, pid) is None:
            session.add(Player(telegram_user_id=pid, username=f"p{pid}"))
    session.flush()
    events.emit(
        session,
        EventType.GAME_WON,
        events.Involved(actor_id=winner, subject_id=host, game_id=game),
        stage=stage,
        hard_mode=hard_mode,
        how="guess",
        pot=0,
        seconds=None,
        last_slot=False,
        distinct_guessers=1,
        winner_wrong=0,
        first_guess=False,
        ended_at=ended.isoformat(),
    )


def _gains(session: Session, game: int) -> dict[PeriodType, PeriodGain]:
    return {g.period.type: g for g in periods.win_gains(session, game, NOW, UTC_TZ)}


def test_a_winner_climbing_from_third_to_first_moves_up_two(session: Session) -> None:
    _win(session, 1, A)
    _win(session, 2, A)  # A 10
    _win(session, 3, B)
    _win(session, 4, B, stage=3)  # B 8
    _win(session, 5, W)  # W 5, third
    _win(session, 6, W, hard_mode=True)  # +6, W 11

    gain = _gains(session, 6)[PeriodType.WEEK]

    assert (gain.gain, gain.score) == (6, 11)
    assert (gain.rank_before, gain.rank_after) == (3, 1)
    assert gain.player_id == W


def test_a_leader_who_keeps_first_place_keeps_their_rank(session: Session) -> None:
    _win(session, 1, A)
    _win(session, 2, B)
    _win(session, 3, A)

    gain = _gains(session, 3)[PeriodType.MONTH]

    assert (gain.rank_before, gain.rank_after) == (1, 1)


def test_a_first_score_has_no_rank_before(session: Session) -> None:
    _win(session, 1, A)

    gain = _gains(session, 1)[PeriodType.YEAR]

    assert gain.rank_before is None
    assert (gain.gain, gain.score, gain.rank_after) == (5, 5, 1)


def test_a_refinish_in_a_closed_period_leaves_that_period_out(session: Session) -> None:
    _win(session, 1, A, ended=datetime(2026, 9, 20, 12, tzinfo=UTC))

    assert set(_gains(session, 1)) == {PeriodType.YEAR}


def test_a_win_that_ended_in_a_past_year_has_no_gains(session: Session) -> None:
    _win(session, 1, A, ended=datetime(2025, 12, 20, 12, tzinfo=UTC))

    assert _gains(session, 1) == {}


def test_a_game_with_no_win_has_no_gains(session: Session) -> None:
    assert periods.win_gains(session, 77, NOW, UTC_TZ) == []


def test_this_games_own_event_is_excluded_from_the_before_side(session: Session) -> None:
    _win(session, 1, A, ended=OCT_3)

    assert PeriodType.WEEK not in _gains(session, 1)  # Oct 3 is last week
    assert _gains(session, 1)[PeriodType.MONTH].rank_before is None


def test_the_host_line_names_the_host(session: Session) -> None:
    _win(session, 1, A, host=B)

    assert win_lines.win_lines(session, 1, "en", NOW)[1] == "Host @p2 +1 🌟"


def test_a_hard_mode_win_has_no_host_line(session: Session) -> None:
    _win(session, 1, A, hard_mode=True)

    lines = win_lines.win_lines(session, 1, "en", NOW)

    assert len(lines) == 1
    assert lines[0].startswith("+6 🌟")


def test_a_win_with_no_running_period_has_no_lines(session: Session) -> None:
    assert win_lines.win_lines(session, 5, "en", NOW) == []


def _gain(ptype: PeriodType, score: int, ranks: tuple[int | None, int]) -> PeriodGain:
    period = periods.period_at(ptype, NOW, UTC_TZ)
    return PeriodGain(period, A, 5, score, ranks[0], ranks[1])


def test_the_gain_line_is_exact_in_english() -> None:
    line = win_lines.gain_line(
        [
            _gain(PeriodType.WEEK, 14, (3, 1)),
            _gain(PeriodType.MONTH, 31, (2, 2)),
            _gain(PeriodType.YEAR, 31, (None, 4)),
        ],
        "en",
    )

    assert line == "+5 🌟 → week 14 (#1 ⬆2) · month 31 (#2 =) · year 31 (#4 🆕)"


def test_the_gain_line_is_exact_in_russian() -> None:
    line = win_lines.gain_line([_gain(PeriodType.WEEK, 14, (2, 1))], "ru")

    assert line == "+5 🌟 → неделя 14 (#1 ⬆1)"


def test_a_computed_downward_move_shows_the_same_marker() -> None:
    assert win_lines.gain_line([_gain(PeriodType.WEEK, 9, (1, 3))], "en").endswith("(#3 =)")


def test_no_gains_make_no_line() -> None:
    assert win_lines.gain_line([], "en") == ""
