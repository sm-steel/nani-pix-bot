import pytest

from nani_pix_bot.models.enums import EventType, Rarity
from nani_pix_bot.services.achievements.definitions import (
    Definition,
    Kind,
    climb,
    exact_tier,
    grants_title,
    next_threshold,
    rarity_of,
    reached,
    threshold,
)

B, S, G, P = Rarity.BRONZE, Rarity.SILVER, Rarity.GOLD, Rarity.PLATINUM


def _zero(_history: object) -> int:
    return 0


def _ladder(endless: int | None = None) -> Definition:
    return Definition(
        "wins", Kind.LADDER, (1, 5, 10), (B, G, P), frozenset({EventType.GAME_WON}), _zero, endless
    )


def test_thresholds_follow_the_defined_tiers_then_the_endless_step() -> None:
    defn = _ladder(endless=100)
    assert [threshold(defn, t) for t in (1, 2, 3, 4, 5)] == [1, 5, 10, 110, 210]


def test_reached_counts_every_tier_at_or_below_the_value() -> None:
    assert [reached(_ladder(), v) for v in (0, 1, 4, 5, 10, 999)] == [0, 1, 1, 2, 3, 3]
    assert [reached(_ladder(endless=100), v) for v in (109, 110, 209, 210)] == [3, 4, 4, 5]


def test_exact_tier_is_only_the_tier_whose_threshold_equals_the_value() -> None:
    defn = _ladder(endless=100)
    assert exact_tier(defn, 5) == 2
    assert exact_tier(defn, 6) is None
    assert exact_tier(defn, 0) is None
    assert exact_tier(defn, 210) == 5


def test_next_threshold_stops_at_the_top_unless_endless() -> None:
    assert next_threshold(_ladder(), 2) == 10
    assert next_threshold(_ladder(), 3) is None
    assert next_threshold(_ladder(endless=100), 3) == 110


def test_endless_tiers_keep_the_last_rarity() -> None:
    defn = _ladder(endless=100)
    assert [rarity_of(defn, t) for t in (1, 2, 3, 7)] == [B, G, P, P]


def test_only_the_top_defined_ladder_tier_grants_a_title() -> None:
    defn = _ladder(endless=100)
    assert [grants_title(defn, t) for t in (1, 2, 3, 4)] == [False, False, True, False]


def test_group_unique_and_period_definitions_always_grant_titles() -> None:
    for kind in (Kind.GROUP_UNIQUE, Kind.PERIOD):
        assert grants_title(Definition("x", kind, (1,), (P,), frozenset(), _zero), 1)


def test_one_shots_never_grant_titles() -> None:
    assert not grants_title(Definition("x", Kind.ONE_SHOT, (1,), (S,), frozenset(), _zero), 1)


@pytest.mark.parametrize(
    ("n", "expected"),
    [
        (2, (G, P)),
        (3, (B, G, P)),
        (4, (B, S, G, P)),
        (5, (B, S, S, G, P)),
        (6, (B, B, S, S, G, P)),
    ],
)
def test_climb_spreads_rarities_and_always_ends_platinum(n: int, expected: tuple) -> None:
    assert climb(n) == expected


def test_a_definition_rejects_mismatched_tiers_and_rarities() -> None:
    with pytest.raises(ValueError, match="rarities"):
        Definition("x", Kind.LADDER, (1, 5), (B,), frozenset(), _zero)


def test_a_definition_rejects_non_increasing_tiers() -> None:
    with pytest.raises(ValueError, match="increasing"):
        Definition("x", Kind.LADDER, (5, 5), (B, S), frozenset(), _zero)
