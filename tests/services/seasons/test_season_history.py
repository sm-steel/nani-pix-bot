from datetime import timedelta

from nani_pix_bot.models.enums import EventType, GameStatus, PixelStage
from nani_pix_bot.models.event_log import EventLog
from nani_pix_bot.models.game import Game
from nani_pix_bot.services.events import LoggedEvent
from nani_pix_bot.services.seasons import season_history
from tests.seasons_fixtures import NOW, STARTER


def _log(session, event_type, *, actor, game_id=None, at=NOW, **data) -> None:
    session.add(
        EventLog(
            event_type=event_type,
            actor_id=actor,
            game_id=game_id,
            data=data,
            occurred_at=at.replace(tzinfo=None),
        )
    )
    session.flush()


def _untagged_game(session) -> Game:
    """A second, season-less game beside `tagged_game` (whose fixture already
    created the players)."""
    game = Game(
        starter_id=STARTER,
        status=GameStatus.ACTIVE,
        current_stage=PixelStage.STAGE_1,
        title_english="Toradora",
    )
    session.add(game)
    session.flush()
    return game


def test_events_of_tagged_games_are_in_scope(session, active_season, tagged_game) -> None:
    _log(session, EventType.GAME_WON, actor=1, game_id=tagged_game.id, stage=2)
    history = season_history.for_player(session, active_season, 1)
    assert len(history.mine(EventType.GAME_WON)) == 1


def test_untagged_games_are_outside_the_scope(session, active_season, untagged_game) -> None:
    _log(session, EventType.GAME_WON, actor=1, game_id=untagged_game.id, stage=2)
    assert season_history.for_player(session, active_season, 1).mine(EventType.GAME_WON) == []


def test_gameless_events_count_only_within_the_season_dates(session, active_season) -> None:
    _log(session, EventType.CURRENCY_MOVED, actor=1, reason="tip")
    _log(session, EventType.CURRENCY_MOVED, actor=1, reason="tip", at=NOW - timedelta(days=5))
    _log(session, EventType.CURRENCY_MOVED, actor=1, reason="tip", at=NOW + timedelta(days=20))
    history = season_history.for_player(session, active_season, 1)
    assert len(history.mine(EventType.CURRENCY_MOVED)) == 1


def test_days_played_are_scoped_too(session, active_season, tagged_game) -> None:
    untagged = _untagged_game(session)
    _log(session, EventType.GUESS, actor=1, game_id=tagged_game.id)
    _log(session, EventType.GUESS, actor=1, game_id=untagged.id, at=NOW + timedelta(days=1))
    assert len(season_history.for_player(session, active_season, 1).my_days(EventType.GUESS)) == 1


def test_group_probes_are_scoped_too(session, active_season, tagged_game) -> None:
    untagged = _untagged_game(session)
    _log(session, EventType.GUESS, actor=2, game_id=untagged.id)
    history = season_history.for_player(session, active_season, 1)
    day = NOW.date()
    assert history.group_active_between((EventType.GUESS,), day, day) is False
    _log(session, EventType.GUESS, actor=2, game_id=tagged_game.id)
    assert history.group_active_between((EventType.GUESS,), day, day) is True


def test_a_late_start_moves_the_window_to_started_at(session, active_season) -> None:
    active_season.started_at = (NOW + timedelta(hours=2)).replace(tzinfo=None)
    session.flush()
    _log(session, EventType.CURRENCY_MOVED, actor=1, reason="tip", at=NOW + timedelta(hours=1))
    _log(session, EventType.CURRENCY_MOVED, actor=1, reason="tip", at=NOW + timedelta(hours=3))
    history = season_history.for_player(session, active_season, 1)
    (inside,) = history.mine(EventType.CURRENCY_MOVED)
    assert inside.occurred_at == NOW + timedelta(hours=3)


def test_season_events_are_in_scope_after_end_at(session, active_season) -> None:
    _log(
        session,
        EventType.TRACK_COMPLETED,
        actor=1,
        at=NOW + timedelta(days=20),
        season_id=active_season.id,
        premium=False,
        first=True,
    )
    history = season_history.for_player(session, active_season, 1)
    assert len(history.mine(EventType.TRACK_COMPLETED)) == 1


def _ended_event(season_id: int) -> LoggedEvent:
    return LoggedEvent(
        id=1,
        event_type=EventType.SEASON_ENDED,
        actor_id=1,
        subject_id=None,
        game_id=None,
        data={"season_id": season_id},
        occurred_at=NOW,
    )


def test_season_of_event_prefers_the_data_season(session, active_season) -> None:
    found = season_history.season_of_event(session, _ended_event(active_season.id))
    assert found is not None
    assert found.id == active_season.id


def test_an_ended_season_is_not_a_season_of_any_event(session, active_season, end_season) -> None:
    end_season()
    assert season_history.season_of_event(session, _ended_event(active_season.id)) is None
