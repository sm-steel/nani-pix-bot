from datetime import UTC, datetime, timedelta

import pytest

from nani_pix_bot.services.economy import rewards
from nani_pix_bot.services.economy.config import DEFAULT_AMOUNTS, EconomyKey

AMOUNTS = dict(DEFAULT_AMOUNTS)


@pytest.mark.parametrize(("stage", "expected"), [(1, 40), (2, 30), (3, 25), (4, 20), (5, 15)])
def test_win_reward_by_stage(stage: int, expected: int) -> None:
    assert rewards.win_reward(AMOUNTS, stage_number=stage) == expected


def test_win_reward_applies_multiplier() -> None:
    assert rewards.win_reward(AMOUNTS, stage_number=2, multiplier=2) == 60


@pytest.mark.parametrize("stage", [0, 6])
def test_win_reward_rejects_out_of_range_stage(stage: int) -> None:
    with pytest.raises(ValueError, match="stage"):
        rewards.win_reward(AMOUNTS, stage_number=stage)


@pytest.mark.parametrize(("earned", "expected"), [(0, 2), (8, 2), (9, 1), (10, 0)])
def test_wrong_guess_reward_stops_exactly_at_cap(earned: int, expected: int) -> None:
    assert rewards.wrong_guess_reward(AMOUNTS, earned_this_game=earned) == expected


def test_wrong_guess_reward_never_negative_after_cap_lowered() -> None:
    amounts = {**AMOUNTS, EconomyKey.WRONG_GUESS_CAP: 4}
    assert rewards.wrong_guess_reward(amounts, earned_this_game=10) == 0


@pytest.mark.parametrize(("stage", "expected"), [(1, False), (2, True), (4, True), (5, False)])
def test_setter_rewarded_only_for_stages_two_to_four(stage: int, expected: bool) -> None:
    assert rewards.setter_rewarded(stage) is expected


def test_is_prompt_start_within_window() -> None:
    received = datetime(2026, 10, 4, 12, 0, tzinfo=UTC)
    assert rewards.is_prompt_start(
        created_at=received + timedelta(minutes=59), turn_received_at=received
    )
    assert rewards.is_prompt_start(
        created_at=received + timedelta(hours=1), turn_received_at=received
    )


def test_is_prompt_start_outside_window() -> None:
    received = datetime(2026, 10, 4, 12, 0, tzinfo=UTC)
    assert not rewards.is_prompt_start(
        created_at=received + timedelta(hours=1, seconds=1), turn_received_at=received
    )


def test_is_prompt_start_handles_naive_and_aware() -> None:
    # as read back from the DB
    received_naive = datetime(2026, 10, 4, 12, 0, tzinfo=UTC).replace(tzinfo=None)
    created_aware = datetime(2026, 10, 4, 12, 30, tzinfo=UTC)
    assert rewards.is_prompt_start(created_at=created_aware, turn_received_at=received_naive)
