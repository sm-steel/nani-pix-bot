import pytest

from nani_pix_bot.models.enums import GameStatus
from nani_pix_bot.models.game import Game
from nani_pix_bot.models.season import SeasonSchedule
from nani_pix_bot.seasons.mechanics import GroupGoal
from nani_pix_bot.services.seasons.group_goal import (
    candles,
    candles_for,
    contributors,
    due_milestone,
    goal_of,
    is_contributor,
)

GOAL = GroupGoal(target=100)


def _g(*, status=GameStatus.WON, haunt=None, event_day=False, boss=False) -> Game:
    return Game(status=status, haunt_twist=haunt, event_day=event_day, boss_game=boss)


def test_candles_for_weights():
    assert candles_for(_g(), GOAL) == 1
    assert candles_for(_g(haunt="kitsune_mirror"), GOAL) == 2
    assert candles_for(_g(haunt="kitsune_mirror", event_day=True), GOAL) == 3
    assert candles_for(_g(boss=True, haunt="kitsune_mirror"), GOAL) == 0
    assert candles_for(_g(status=GameStatus.ACTIVE), GOAL) == 0


def test_candles_sums_only_finished_games_of_this_season(session, yokai_season, finished_game):
    finished_game(GameStatus.WON)
    finished_game(GameStatus.UNSOLVED, haunt_twist="kitsune_mirror")
    finished_game(GameStatus.WON, event_day=True)
    finished_game(GameStatus.WON, hard_mode=True)
    finished_game(GameStatus.WON, boss_game=True)
    finished_game(GameStatus.ACTIVE)
    finished_game(GameStatus.WON, season_id=999)
    finished_game(GameStatus.WON, season_id=None)
    assert candles(session, yokai_season.id, GOAL) == 7


def test_contributors_are_guessers_hosts_and_winners(session, yokai_season, finished_game):
    finished_game(GameStatus.WON, starter_id=2, guessers=(3, 4), winner_id=3)
    finished_game(GameStatus.WON, starter_id=10, hard_mode=True, guessers=(5,), winner_id=6)
    finished_game(GameStatus.WON, starter_id=11, boss_game=True, guessers=(7,))
    finished_game(GameStatus.ACTIVE, starter_id=12, guessers=(8,))
    assert contributors(session, yokai_season.id) == {2, 3, 4, 5, 6}
    assert is_contributor(session, yokai_season.id, 4)
    assert not is_contributor(session, yokai_season.id, 7)


@pytest.mark.parametrize(
    ("snuffed", "last", "expected"),
    [
        (24, 0, None),
        (25, 0, 25),
        (51, 25, 50),
        (60, 50, None),
        (99, 75, None),
        (100, 75, None),
        (80, 0, 75),
    ],
)
def test_due_milestone(snuffed, last, expected):
    assert due_milestone(snuffed, GroupGoal(target=100), last) == expected


def test_no_milestones_when_every_is_zero():
    assert due_milestone(50, GroupGoal(target=100, milestone_every=0), 0) is None


def test_goal_of_is_none_without_a_group_goal(session, active_season):
    assert goal_of(session, active_season.id) is None


def test_goal_of_returns_the_run_goal(session, yokai_season):
    found = goal_of(session, yokai_season.id)
    assert found is not None
    season, run, goal = found
    assert (season.id, run.group_goal, goal.target) == (yokai_season.id, goal, 10)


def test_goal_of_none_for_missing_season(session):
    assert goal_of(session, None) is None
    assert goal_of(session, 4242) is None


def test_goal_of_none_for_unknown_run(session, yokai_season):
    row = session.get(SeasonSchedule, yokai_season.id)
    row.run_id = "nope"
    session.flush()
    assert goal_of(session, yokai_season.id) is None
