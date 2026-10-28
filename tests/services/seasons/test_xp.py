from dataclasses import replace
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select

from nani_pix_bot.models.currency_transfer import CurrencyTransfer
from nani_pix_bot.models.enums import (
    CurrencyReason,
    EventType,
    GameStatus,
    PixelStage,
    SeasonStatus,
    XpSource,
)
from nani_pix_bot.models.event_log import EventLog
from nani_pix_bot.models.game import Game
from nani_pix_bot.models.game_guess import GameGuess
from nani_pix_bot.models.player import Player
from nani_pix_bot.models.season import SeasonSchedule, SeasonXp
from nani_pix_bot.seasons import registry
from nani_pix_bot.seasons.definition import XpTable
from nani_pix_bot.services import events
from nani_pix_bot.services import game as game_service
from nani_pix_bot.services.events import LoggedEvent
from nani_pix_bot.services.seasons import consumer, xp
from tests import seasons_fixtures

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
    """Game XP only: an objective a win completes adds its own row (test_objectives.py)."""
    rows = session.scalars(
        select(SeasonXp).where(SeasonXp.source != XpSource.OBJECTIVE).order_by(SeasonXp.id)
    )
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


@pytest.mark.parametrize("status", [SeasonStatus.ENDED, SeasonStatus.CANCELLED])
def test_a_finished_seasons_games_earn_nothing_more(session, status, log_records) -> None:
    season = _season(session, status)
    game = _game(session, season.id)
    consumer.on_event(
        session,
        _event(EventType.GAME_WON, actor=ALICE, subject=STARTER, game_id=game.id, stage=1),
    )
    assert _xp_rows(session) == []
    skipped = [r for r in log_records if "already over" in r.message]
    assert skipped
    assert skipped[0].extra["season_id"] == season.id
    assert skipped[0].extra["game_id"] == game.id


def test_a_closing_seasons_late_game_still_pays(session) -> None:
    season = _season(session, SeasonStatus.CLOSING)
    game = _game(session, season.id)
    consumer.on_event(
        session,
        _event(EventType.GAME_WON, actor=ALICE, subject=STARTER, game_id=game.id, stage=1),
    )
    assert _xp_rows(session) == [(ALICE, 50, "win"), (STARTER, 15, "host")]


def test_a_win_after_an_unsolved_end_pays_the_host_only_the_difference() -> None:
    got = xp.awards(
        xp.host_top_up(TABLE),
        _event(EventType.GAME_WON, actor=ALICE, subject=STARTER, stage=2),
        bot_game=False,
        first_guess_of_player=False,
    )
    assert got == [xp.Award(ALICE, 40, XpSource.WIN), xp.Award(STARTER, 10, XpSource.HOST)]


def test_the_host_top_up_is_never_negative() -> None:
    table = XpTable(
        win_by_stage=(50, 40, 30, 20, 10), host_solved=5, host_unsolved=8, first_guess=5
    )
    got = xp.awards(
        xp.host_top_up(table),
        _event(EventType.GAME_WON, actor=ALICE, subject=STARTER, stage=2),
        bot_game=False,
        first_guess_of_player=False,
    )
    assert got == [xp.Award(ALICE, 40, XpSource.WIN)]


_COIN_REASONS = {XpSource.WIN: CurrencyReason.SEASON_WIN, XpSource.HOST: CurrencyReason.SEASON_HOST}


def _game_award(session, game_id: int, player: int, source: XpSource) -> tuple[int, int]:
    """The XP and 🪙 one game paid `player` as its winner or host."""
    xp_total = sum(
        session.scalars(
            select(SeasonXp.amount).where(
                SeasonXp.game_id == game_id,
                SeasonXp.player_id == player,
                SeasonXp.source == source,
            )
        )
    )
    coins_total = sum(
        session.scalars(
            select(CurrencyTransfer.amount).where(
                CurrencyTransfer.game_id == game_id,
                CurrencyTransfer.to_player_id == player,
                CurrencyTransfer.reason == _COIN_REASONS[source],
            )
        )
    )
    return xp_total, coins_total


def test_setwinner_tops_the_host_up_to_solved(session, tagged_game, log_records) -> None:
    """An admin /setwinner on a tagged game that already ended UNSOLVED (and so
    paid the host `host_unsolved`) leaves the host with exactly a solved game's
    XP and 🪙, through the real game services and the real consumer."""
    run = registry.all_runs()["demo_1"]
    host, winner = tagged_game.starter_id, seasons_fixtures.WINNER

    game_service.force_unsolved(tagged_game, cause="timeout")
    assert _game_award(session, tagged_game.id, host, XpSource.HOST) == (
        run.xp.host_unsolved,
        run.coins.host_unsolved,
    )

    game_service.refinish(session, tagged_game, winner_id=winner)

    assert _game_award(session, tagged_game.id, host, XpSource.HOST) == (
        run.xp.host_solved,
        run.coins.host_solved,
    )
    # The winner's own award is a solved game's, untouched by the top-up.
    assert _game_award(session, tagged_game.id, winner, XpSource.WIN) == (
        run.xp.win_by_stage[0],
        run.coins.win,
    )
    line = next(r for r in log_records if "topped the host up to solved" in r.message)
    assert line.level == "INFO"
    assert line.extra["game_id"] == tagged_game.id
    assert line.extra["recipient_id"] == host
    assert (line.extra["xp"], line.extra["coins"]) == (
        run.xp.host_solved - run.xp.host_unsolved,
        run.coins.host_solved - run.coins.host_unsolved,
    )


def _top_up_lines(log_records) -> list:
    return [r for r in log_records if "topped the host up to solved" in r.message]


def test_a_zero_top_up_is_not_logged(
    session, tagged_game, fake_runs, monkeypatch, log_records
) -> None:
    """A run whose solved host award is no more than the unsolved one tops up
    nothing, so there is nothing to log."""
    run = fake_runs["demo_1"]
    flat = replace(
        run,
        xp=replace(run.xp, host_solved=run.xp.host_unsolved),
        coins=replace(run.coins, host_solved=run.coins.host_unsolved),
    )
    monkeypatch.setitem(fake_runs, "demo_1", flat)
    game_service.force_unsolved(tagged_game, cause="timeout")

    game_service.refinish(session, tagged_game, winner_id=seasons_fixtures.WINNER)

    host = tagged_game.starter_id
    assert _game_award(session, tagged_game.id, host, XpSource.HOST) == (
        run.xp.host_unsolved,
        run.coins.host_unsolved,
    )
    assert _top_up_lines(log_records) == []


def test_a_hard_mode_setwinner_pays_no_host_and_logs_no_top_up(
    session, bot_tagged_game, log_records
) -> None:
    game_service.force_unsolved(bot_tagged_game, cause="timeout")

    game_service.refinish(session, bot_tagged_game, winner_id=seasons_fixtures.WINNER)

    host = bot_tagged_game.starter_id
    assert _game_award(session, bot_tagged_game.id, host, XpSource.HOST) == (0, 0)
    assert _top_up_lines(log_records) == []


def test_a_boss_win_multiplies_guesser_xp_and_pays_no_host(
    session, haunted_game, emit_win, log_records
) -> None:
    demo3 = registry.get("demo_3")
    assert demo3 is not None
    assert demo3.group_goal is not None
    haunted_game.boss_game = True
    emit_win(winner=1, stage=3)
    totals = {p.player_id: p.xp for p in xp.standings(session, haunted_game.season_id)}
    # Nani Pix started the boss: no human host, so no host XP.
    assert totals == {1: demo3.xp.win_by_stage[2] * demo3.group_goal.boss_xp_multiplier}
    lines = [r for r in log_records if r.level == "INFO" and "season XP" in r.message]
    assert {r.extra["recipient_id"]: r.extra["multiplier"] for r in lines} == {
        1: demo3.group_goal.boss_xp_multiplier,
    }
