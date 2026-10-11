from dataclasses import replace
from datetime import timedelta

import pytest
from sqlalchemy import select

from nani_pix_bot.models.achievement import AchievementGrant
from nani_pix_bot.models.enums import EventType, GameStatus, PixelStage, Rarity, SeasonStatus
from nani_pix_bot.models.game import Game
from nani_pix_bot.models.season import SeasonSchedule
from nani_pix_bot.services import events
from nani_pix_bot.services.achievements import catalogue, engine, names, status
from nani_pix_bot.services.achievements.definitions import Definition, Kind, grants_title
from nani_pix_bot.services.achievements.status import State
from tests.seasons_fixtures import NOW, STARTER, WINNER

pytestmark = pytest.mark.achievements


def _wins(history) -> int:
    return len(history.mine(EventType.GAME_WON))


TEST_DEF = Definition(
    "test_season_win", Kind.SEASON, (1,), (Rarity.BRONZE,), frozenset({EventType.GAME_WON}), _wins
)
# Two wins in one season: one win leaves it partial.
TWO_WINS = Definition(
    "test_season_two_wins",
    Kind.SEASON,
    (2,),
    (Rarity.SILVER,),
    frozenset({EventType.GAME_WON}),
    _wins,
)


@pytest.fixture(autouse=True)
def _inject(monkeypatch, fake_runs):
    # Only the test definitions: the static ones read GAME_WON fields these
    # fixtures' minimal wins don't carry.
    monkeypatch.setattr(catalogue, "CATALOGUE", ())
    monkeypatch.setattr(catalogue, "_run_definitions", lambda: (TEST_DEF, TWO_WINS))


@pytest.fixture
def second_season_game(session) -> Game:
    """A game tagged to a new ACTIVE season of run demo_2."""
    season = SeasonSchedule(
        run_id="demo_2",
        start_at=NOW,
        end_at=NOW + timedelta(days=9),
        status=SeasonStatus.ACTIVE,
        created_by=9,
    )
    session.add(season)
    session.flush()
    game = Game(
        starter_id=STARTER,
        status=GameStatus.ACTIVE,
        current_stage=PixelStage.STAGE_1,
        title_english="Naruto",
        season_id=season.id,
    )
    session.add(game)
    session.flush()
    return game


def _grants(session, key: str = TEST_DEF.key) -> list[tuple[int, int, str]]:
    rows = session.scalars(select(AchievementGrant).where(AchievementGrant.key == key))
    return [(r.player_id, r.tier, r.period_key) for r in rows]


def test_a_season_achievement_is_granted_with_the_run_as_period(
    session, tagged_game, emit_win
) -> None:
    emit_win(winner=WINNER, stage=2)
    assert _grants(session) == [(WINNER, 1, "demo_1")]


def test_a_season_achievement_is_earned_once_per_season(session, tagged_game, emit_win) -> None:
    emit_win(winner=WINNER, stage=2)
    emit_win(winner=WINNER, stage=3)
    assert len(_grants(session)) == 1


def test_untagged_games_earn_nothing(session, untagged_game, emit_win) -> None:
    emit_win(winner=WINNER, stage=2)
    assert _grants(session) == []


def test_it_is_earned_again_in_another_season(
    session, tagged_game, emit_win, end_season, second_season_game
) -> None:
    emit_win(winner=WINNER, stage=2)
    end_season()
    events.emit(
        session,
        EventType.GAME_WON,
        events.Involved(actor_id=WINNER, game_id=second_season_game.id),
        stage=1,
        hard_mode=False,
    )
    assert sorted(p for _, _, p in _grants(session)) == ["demo_1", "demo_2"]


def test_progress_counts_only_the_events_season(
    session, tagged_game, emit_win, end_season, second_season_game
) -> None:
    emit_win(winner=WINNER, stage=2)
    end_season()
    events.emit(
        session,
        EventType.GAME_WON,
        events.Involved(actor_id=WINNER, game_id=second_season_game.id),
        stage=1,
        hard_mode=False,
    )
    assert _grants(session, TWO_WINS.key) == []


def test_nothing_once_the_season_ended(session, tagged_game, emit_win, end_season) -> None:
    end_season()
    emit_win(winner=WINNER, stage=2)
    assert _grants(session) == []


def test_a_run_bound_definition_counts_only_in_its_run(
    session, tagged_game, emit_win, monkeypatch
) -> None:
    bound = replace(TEST_DEF, run_id="demo_2")
    monkeypatch.setattr(catalogue, "_run_definitions", lambda: (bound,))
    emit_win(winner=WINNER, stage=2)
    assert _grants(session) == []


def test_titled_season_definitions_grant_a_title() -> None:
    assert grants_title(replace(TEST_DEF, titled=True), 1) is True
    assert grants_title(TEST_DEF, 1) is False


def test_only_season_definitions_may_be_titled() -> None:
    with pytest.raises(ValueError, match="only season achievements"):
        Definition("x", Kind.LADDER, (1,), (Rarity.BRONZE,), frozenset(), _wins, titled=True)


def test_the_season_title_names_the_run(monkeypatch) -> None:
    defn = replace(TEST_DEF, titled=True)
    monkeypatch.setattr(names.i18n, "t", lambda key, lang, **kwargs: f"{key}|{kwargs}")
    rendered = names.title(defn, 1, "demo_1", "EN")
    assert rendered.startswith("achievement.test_season_win.title|")
    assert "Demo Season" in rendered


def test_the_season_title_falls_back_to_the_run_id(monkeypatch) -> None:
    defn = replace(TEST_DEF, titled=True)
    monkeypatch.setattr(names.i18n, "t", lambda key, lang, **kwargs: f"{key}|{kwargs}")
    assert "gone_run" in names.title(defn, 1, "gone_run", "EN")


def test_definitions_add_the_run_definitions_to_the_catalogue() -> None:
    assert catalogue.definitions() == (*catalogue.CATALOGUE, TEST_DEF, TWO_WINS)
    assert catalogue.get(TEST_DEF.key) is TEST_DEF
    assert TEST_DEF in catalogue.triggered_by(EventType.GAME_WON)


def _status_of(session, key: str) -> status.Status:
    return next(s for s in status.build(session, WINNER, NOW) if s.defn.key == key)


def test_an_earned_season_achievement_counts_its_seasons(session, untagged_game) -> None:
    for run_id in ("demo_1", "demo_2"):
        engine.grant(session, engine.GrantRequest(WINNER, TEST_DEF.key, 1, run_id))

    item = _status_of(session, TEST_DEF.key)

    assert (item.state, item.tier, item.rank) == (State.EARNED, 2, None)


def test_an_unearned_one_shows_progress_in_the_open_season(session, tagged_game, emit_win) -> None:
    emit_win(winner=WINNER, stage=2)

    item = _status_of(session, TWO_WINS.key)

    assert (item.state, item.value, item.target) == (State.PARTIAL, 1, 2)


def test_an_unearned_one_is_untouched_with_no_open_season(
    session, tagged_game, emit_win, end_season
) -> None:
    emit_win(winner=WINNER, stage=2)
    end_season()

    item = _status_of(session, TWO_WINS.key)

    assert (item.state, item.value) == (State.UNTOUCHED, 0)


def test_a_run_bound_one_is_not_listed_in_another_runs_season(
    session, tagged_game, emit_win, monkeypatch
) -> None:
    bound = replace(TWO_WINS, run_id="demo_2")
    monkeypatch.setattr(catalogue, "_run_definitions", lambda: (bound,))
    emit_win(winner=WINNER, stage=2)

    assert TWO_WINS.key not in [s.defn.key for s in status.build(session, WINNER, NOW)]


def test_a_hidden_unearned_one_stays_hidden(session, tagged_game, monkeypatch) -> None:
    hidden = replace(TWO_WINS, hidden=True)
    monkeypatch.setattr(catalogue, "_run_definitions", lambda: (hidden,))

    assert _status_of(session, TWO_WINS.key).state is State.HIDDEN
