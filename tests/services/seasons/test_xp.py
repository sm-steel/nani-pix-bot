from datetime import UTC, datetime, timedelta

from sqlalchemy import select

from nani_pix_bot.models.enums import EventType, GameStatus, PixelStage, SeasonStatus, XpSource
from nani_pix_bot.models.event_log import EventLog
from nani_pix_bot.models.game import Game
from nani_pix_bot.models.game_guess import GameGuess
from nani_pix_bot.models.player import Player
from nani_pix_bot.models.season import SeasonSchedule, SeasonXp
from nani_pix_bot.seasons.definition import XpTable
from nani_pix_bot.services import events
from nani_pix_bot.services.events import LoggedEvent
from nani_pix_bot.services.seasons import consumer, xp

TABLE = XpTable(win_by_stage=(50, 40, 30, 20, 10), host_solved=15, host_unsolved=5, first_guess=5)
NOW = datetime(2026, 11, 1, tzinfo=UTC)
STARTER, ALICE, BOB = 1, 2, 3


def _event(event_type, *, actor=None, subject=None, game_id=1, **data) -> LoggedEvent:
    return LoggedEvent(1, event_type, actor, subject, game_id, data, NOW)


def test_win_pays_the_winner_by_stage_and_the_host() -> None:
    got = xp.awards(
        TABLE,
        _event(EventType.GAME_WON, actor=ALICE, subject=STARTER, stage=2),
        bot_game=False,
        first_guess_of_player=False,
    )
    assert got == [xp.Award(ALICE, 40, XpSource.WIN), xp.Award(STARTER, 15, XpSource.HOST)]


def test_bot_games_pay_no_host_xp_and_hard_mode_turns_map_to_stages() -> None:
    got = xp.awards(
        TABLE,
        _event(EventType.GAME_WON, actor=ALICE, subject=STARTER, stage=1),
        bot_game=True,
        first_guess_of_player=False,
    )
    assert got == [xp.Award(ALICE, 50, XpSource.WIN)]


def test_unknown_stage_pays_no_win_xp() -> None:
    got = xp.awards(
        TABLE,
        _event(EventType.GAME_WON, actor=ALICE, subject=STARTER, stage=0),
        bot_game=True,
        first_guess_of_player=False,
    )
    assert got == []


def test_unsolved_pays_the_host_a_little() -> None:
    got = xp.awards(
        TABLE,
        _event(EventType.GAME_UNSOLVED, subject=STARTER),
        bot_game=False,
        first_guess_of_player=False,
    )
    assert got == [xp.Award(STARTER, 5, XpSource.HOST)]


def test_only_a_players_first_guess_in_a_game_pays() -> None:
    first = xp.awards(
        TABLE, _event(EventType.GUESS, actor=BOB), bot_game=False, first_guess_of_player=True
    )
    later = xp.awards(
        TABLE, _event(EventType.GUESS, actor=BOB), bot_game=False, first_guess_of_player=False
    )
    assert first == [xp.Award(BOB, 5, XpSource.FIRST_GUESS)]
    assert later == []


def _season(session, status=SeasonStatus.ACTIVE) -> SeasonSchedule:
    row = SeasonSchedule(
        run_id="demo_1",
        start_at=NOW - timedelta(days=1),
        end_at=NOW + timedelta(days=9),
        status=status,
        created_by=9,
    )
    session.add(row)
    session.flush()
    return row


def _game(session, season_id) -> Game:
    session.add_all([Player(telegram_user_id=u) for u in (STARTER, ALICE, BOB)])
    game = Game(
        starter_id=STARTER,
        status=GameStatus.ACTIVE,
        current_stage=PixelStage.STAGE_1,
        title_english="Clannad",
        season_id=season_id,
    )
    session.add(game)
    session.flush()
    return game


def _xp_rows(session) -> list[tuple[int, int, str]]:
    rows = session.scalars(select(SeasonXp).order_by(SeasonXp.id))
    return [(r.player_id, r.amount, r.source) for r in rows]


def test_events_of_a_tagged_game_write_xp_rows(session) -> None:
    season = _season(session)
    game = _game(session, season.id)
    session.add(GameGuess(game_id=game.id, player_id=BOB, text="x", stage=1, correct=False))
    session.flush()
    consumer.on_event(session, _event(EventType.GUESS, actor=BOB, game_id=game.id))
    consumer.on_event(
        session,
        _event(EventType.GAME_WON, actor=ALICE, subject=STARTER, game_id=game.id, stage=3),
    )
    assert _xp_rows(session) == [(BOB, 5, "first_guess"), (ALICE, 30, "win"), (STARTER, 15, "host")]


def test_untagged_games_earn_nothing(session) -> None:
    game = _game(session, None)
    consumer.on_event(
        session,
        _event(EventType.GAME_WON, actor=ALICE, subject=STARTER, game_id=game.id, stage=1),
    )
    assert _xp_rows(session) == []


def test_standings_rank_by_xp_with_shared_places(session) -> None:
    season = _season(session)
    session.add_all([Player(telegram_user_id=u) for u in (STARTER, ALICE, BOB)])
    session.add_all(
        [
            SeasonXp(season_id=season.id, player_id=ALICE, amount=40, source=XpSource.WIN),
            SeasonXp(season_id=season.id, player_id=BOB, amount=40, source=XpSource.WIN),
            SeasonXp(season_id=season.id, player_id=STARTER, amount=15, source=XpSource.HOST),
        ]
    )
    session.flush()
    assert xp.standings(session, season.id) == [
        xp.Placed(1, ALICE, 40),
        xp.Placed(1, BOB, 40),
        xp.Placed(3, STARTER, 15),
    ]


def test_a_broken_season_consumer_never_rolls_back_the_event(
    session, monkeypatch, log_records
) -> None:
    calls = []

    def boom(*_a):
        calls.append(1)
        raise RuntimeError("x")

    monkeypatch.setattr(consumer, "on_event", boom)
    event = events.emit(session, EventType.OVERTHROWN, events.Involved())
    assert calls
    assert any(
        r.level == "ERROR" and r.message.startswith("seasons failed on event") for r in log_records
    )
    assert session.get(EventLog, event.id) is not None


def test_emit_runs_the_seasons_consumer(session) -> None:
    season = _season(session)
    game = _game(session, season.id)
    events.emit(
        session,
        EventType.GAME_WON,
        events.Involved(actor_id=ALICE, subject_id=STARTER, game_id=game.id),
        stage=1,
    )
    assert _xp_rows(session) == [(ALICE, 50, "win"), (STARTER, 15, "host")]
