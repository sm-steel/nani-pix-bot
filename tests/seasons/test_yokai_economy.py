"""Yōkai Night's economy holds the targets the user approved, counting no luck
(haunted x2, Night Parade, the boss, omikuji, contributor coins, hex refunds),
and the 100 Candles goal is paced so the boss comes after the Night Parade.
Every number is read from RUN; only the player profiles and the pace are typed."""

import random
from dataclasses import dataclass

from nani_pix_bot.seasons.definition import GoalKind, RewardKind
from nani_pix_bot.seasons.registry import discover

RUN = discover("nani_pix_bot.seasons.runs")["yokai_1"]
PACE = 7  # finished games a day (Sakura: 80 games in 10.5 days)


@dataclass(frozen=True)
class Profile:
    games: int  # guessed in, the first guess each time
    win_stages: tuple[int, ...]
    hosted: int
    solved: int


CONSERVATIVE = Profile(games=17, win_stages=(2, 3, 3, 3), hosted=4, solved=3)
TYPICAL = Profile(games=20, win_stages=(2,) * 6, hosted=6, solved=5)
CONSERVATIVE_DAYS = 6  # days the conservative player plays on
LUCK_GOALS = {GoalKind.HAUNTED_WINS, GoalKind.HEXES_BLOCKED}


def _sure_objectives():
    return [o for o in RUN.objectives if o.goal.kind not in LUCK_GOALS]


def _xp_and_coins(profile: Profile, objectives) -> tuple[int, int]:
    """One player's season: first guess in every game guessed in, a win at each
    of `win_stages`, hosting with `solved` solved, the given objectives done."""
    games, win_stages = profile.games, profile.win_stages
    hosted, solved = profile.hosted, profile.solved
    xp = games * RUN.xp.first_guess + sum(RUN.xp.win_by_stage[s - 1] for s in win_stages)
    xp += solved * RUN.xp.host_solved + (hosted - solved) * RUN.xp.host_unsolved
    coins = games * RUN.coins.first_guess + len(win_stages) * RUN.coins.win
    coins += solved * RUN.coins.host_solved + (hosted - solved) * RUN.coins.host_unsolved
    xp += sum(o.xp for o in objectives)
    coins += sum(o.coins for o in objectives)
    return xp, coins


def _free_coins_reached(xp: int) -> int:
    rewards = [t.free for t in RUN.tiers if t.xp <= xp]
    return sum(r.amount for r in rewards if r is not None and r.kind is RewardKind.COINS)


def test_the_conservative_profile_is_consistent_with_its_objectives() -> None:
    reach = {
        GoalKind.WINS: len(CONSERVATIVE.win_stages),
        GoalKind.HOSTED: CONSERVATIVE.hosted,
        GoalKind.GUESSED_IN: CONSERVATIVE.games,
        GoalKind.DAYS_PLAYED: CONSERVATIVE_DAYS,
    }
    sure = _sure_objectives()
    assert len(sure) == 5
    for objective in sure:
        assert objective.goal.target <= reach[objective.goal.kind], objective.key
        if objective.goal.max_stage is not None:
            assert min(CONSERVATIVE.win_stages) <= objective.goal.max_stage


def test_a_conservative_free_player_can_afford_the_whole_shop() -> None:
    xp, coins = _xp_and_coins(CONSERVATIVE, _sure_objectives())
    shop = sum(o.price * o.limit for o in RUN.shop)
    assert shop == 790
    assert coins + _free_coins_reached(xp) >= shop + 50


def test_hexes_are_optional_spending() -> None:
    allowed = (RewardKind.ITEM, RewardKind.CLUE_TICKET, RewardKind.OFUDA)
    assert all(o.kind in allowed for o in RUN.shop)


def test_a_typical_regular_finishes_the_pass() -> None:
    xp, _ = _xp_and_coins(TYPICAL, _sure_objectives())
    assert xp >= RUN.tiers[-1].xp


def _goal():
    assert RUN.group_goal is not None
    return RUN.group_goal


def test_candles_reach_the_target_without_any_haunt() -> None:
    goal = _goal()
    days = 15  # 2026-10-28 06:00 → 2026-11-12 06:00, the Night Parade inside it
    floor = (days - 1) * PACE * goal.per_game + PACE * goal.per_event_day
    assert floor >= goal.target * 110 // 100


def _sampled_season(rng: random.Random, days: int) -> int:
    goal = _goal()
    assert RUN.haunt is not None
    total = PACE * goal.per_event_day
    for _ in range((days - 1) * PACE):
        haunted = rng.randrange(100) < RUN.haunt.chance_pct
        total += goal.per_haunted if haunted else goal.per_game
    return total


def test_candles_at_the_haunt_rate_have_margin() -> None:
    rng = random.Random(1031)  # noqa: S311 - deterministic test rng, not crypto
    totals = sorted(_sampled_season(rng, days=14) for _ in range(2000))
    assert totals[len(totals) // 20] >= _goal().target * 110 // 100  # 5th percentile


def test_the_boss_cannot_come_on_or_before_night_parade() -> None:
    goal = _goal()
    before = 3 * PACE * goal.per_haunted  # Oct 28-30, every game haunted (worst case)
    assert before + PACE * goal.per_event_day < goal.target
