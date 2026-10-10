from datetime import UTC, datetime, timedelta

from nani_pix_bot.models.enums import EventType, GameStatus, PixelStage, SeasonStatus, XpSource
from nani_pix_bot.models.game import Game
from nani_pix_bot.models.player import Player
from nani_pix_bot.models.season import SeasonSchedule, SeasonXp
from nani_pix_bot.services.events import LoggedEvent
from nani_pix_bot.services.seasons import consumer, lifecycle

NOW = datetime(2026, 11, 10, 12, 0, tzinfo=UTC)


def _season(session, *, start, end, status=SeasonStatus.SCHEDULED) -> SeasonSchedule:
    row = SeasonSchedule(run_id="demo_1", start_at=start, end_at=end, status=status, created_by=9)
    session.add(row)
    session.flush()
    return row


def _statuses(transitions) -> list[str]:
    return [t.status.value for t in transitions]


def test_nothing_happens_before_the_start(session) -> None:
    row = _season(session, start=NOW + timedelta(hours=1), end=NOW + timedelta(days=1))
    assert lifecycle.advance(session, NOW) == []
    assert lifecycle.next_boundary(session) == NOW + timedelta(hours=1)
    assert row.status == SeasonStatus.SCHEDULED


def test_start_makes_it_active_and_the_end_is_next(session) -> None:
    row = _season(session, start=NOW - timedelta(minutes=1), end=NOW + timedelta(days=1))
    assert _statuses(lifecycle.advance(session, NOW)) == ["active"]
    assert row.started_at is not None
    assert lifecycle.next_boundary(session) == NOW + timedelta(days=1)


def test_catch_up_runs_every_due_step_in_one_pass(session) -> None:
    row = _season(session, start=NOW - timedelta(days=3), end=NOW - timedelta(days=1))
    assert _statuses(lifecycle.advance(session, NOW)) == ["active", "closing", "ended"]
    assert row.status == SeasonStatus.ENDED
    assert lifecycle.next_boundary(session) is None


def test_closing_waits_for_tagged_games_then_finalizes(session) -> None:
    row = _season(
        session,
        start=NOW - timedelta(days=3),
        end=NOW - timedelta(days=1),
        status=SeasonStatus.ACTIVE,
    )
    session.add_all([Player(telegram_user_id=u) for u in (1, 2)])
    game = Game(
        starter_id=1,
        status=GameStatus.ACTIVE,
        current_stage=PixelStage.STAGE_2,
        title_english="Clannad",
        season_id=row.id,
    )
    session.add(game)
    session.flush()
    assert _statuses(lifecycle.advance(session, NOW)) == ["closing"]
    assert lifecycle.next_boundary(session) is None

    # The late win still pays XP into this season, then closes it.
    game.status = GameStatus.WON
    session.add(
        SeasonXp(season_id=row.id, player_id=2, amount=40, source=XpSource.WIN, game_id=game.id)
    )
    session.flush()
    consumer.on_event(session, LoggedEvent(1, EventType.GAME_WON, 2, 1, game.id, {"stage": 9}, NOW))
    assert row.status == SeasonStatus.ENDED
    assert [(r.rank, r.player_id) for r in lifecycle.podium(session, row.id)][:1] == [(1, 2)]


def test_finalize_freezes_the_standings(session) -> None:
    row = _season(
        session,
        start=NOW - timedelta(days=3),
        end=NOW - timedelta(minutes=1),
        status=SeasonStatus.ACTIVE,
    )
    session.add_all([Player(telegram_user_id=u) for u in (1, 2)])
    session.add_all(
        [
            SeasonXp(season_id=row.id, player_id=1, amount=10, source=XpSource.HOST),
            SeasonXp(season_id=row.id, player_id=2, amount=50, source=XpSource.WIN),
        ]
    )
    session.flush()
    lifecycle.advance(session, NOW)
    assert [(r.rank, r.player_id, r.xp) for r in lifecycle.podium(session, row.id)] == [
        (1, 2, 50),
        (2, 1, 10),
    ]
