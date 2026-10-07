from datetime import UTC, datetime
from typing import Any

from nani_pix_bot.models.enums import EventType as E
from nani_pix_bot.services.achievements import ladders
from nani_pix_bot.services.events import LoggedEvent
from tests.services.achievements.fake_history import FakeHistory, ev

ME, OTHER, HOST = 1, 2, 9


def h(*events: LoggedEvent) -> FakeHistory:
    return FakeHistory(ME, list(events))


def won(winner: int, game: int, host: int = HOST, **data: Any) -> LoggedEvent:
    facts = {"stage": 3, "hard_mode": False, "how": "guess", "distinct_guessers": 2} | data
    return ev(E.GAME_WON, winner, host, game=game, **facts)


def money(actor: int | None, subject: int | None, amount: int, reason: str, **extra: Any):
    return ev(
        E.CURRENCY_MOVED,
        actor,
        subject,
        game=extra.get("game"),
        amount=amount,
        reason=reason,
        reversal=extra.get("reversal", False),
    )


def guess(game: int, **data: Any) -> LoggedEvent:
    return ev(E.GUESS, ME, game=game, correct=False, **data)


def test_wins_counts_distinct_games_so_a_refinish_counts_once() -> None:
    history = h(won(ME, 1), won(ME, 1, how="setwinner"), won(ME, 2), won(OTHER, 3))
    assert ladders.wins(history) == 2


def test_hosted_solved_skips_hard_mode_games() -> None:
    history = h(won(OTHER, 1, host=ME), won(OTHER, 2, host=ME, hard_mode=True))
    assert ladders.hosted_solved(history) == 1


def test_games_guessed_is_distinct_games_not_raw_guesses() -> None:
    assert ladders.games_guessed(h(guess(1), guess(1), guess(1), guess(2))) == 2


def test_earned_ignores_reversals_and_counts_tips_and_rewards() -> None:
    history = h(
        money(None, ME, 40, "win"),
        money(OTHER, ME, 10, "tip"),
        money(None, ME, 25, "achievement"),
        money(None, ME, 60, "refund", reversal=True),
        money(None, ME, 30, "cashback", reversal=True),
    )
    assert ladders.earned(history) == 75


def test_spent_counts_clues_sharpen_and_settled_bounties_only() -> None:
    history = h(
        money(ME, None, 100, "clue_purchase"),
        money(ME, None, 250, "sharpen"),
        money(ME, None, 60, "bounty", game=5),  # not counted until it settles
        money(ME, OTHER, 7, "tip"),
        ev(E.BOUNTY_SETTLED, ME, OTHER, game=4, amount=30),
    )
    assert ladders.spent(history) == 380


def test_a_refunded_clue_still_counts_as_bought() -> None:
    history = h(
        ev(E.CLUE_PURCHASED, ME, game=1, kind="tile"),
        ev(E.CLUE_REFUNDED, ME, game=1, kind="tile"),
    )
    assert ladders.clues_bought(history) == 1


def test_tipped_sums_tips_sent() -> None:
    assert ladders.tipped(h(money(ME, OTHER, 5, "tip"), money(OTHER, ME, 9, "tip"))) == 5


def _act(day: int, actor: int = ME) -> LoggedEvent:
    return ev(E.GUESS, actor, game=day, at=datetime(2026, 10, day, 12, tzinfo=UTC), correct=False)


def test_active_days_counts_distinct_local_days() -> None:
    assert ladders.active_days(h(_act(1), _act(1), _act(3))) == 2


def test_hosting_a_hard_mode_game_is_not_activity() -> None:
    bot_game = ev(E.GAME_ACTIVATED, ME, game=1, hard_mode=True, source="tenrai")
    assert ladders.active_days(h(bot_game)) == 0


def test_unbroken_skips_days_nobody_played() -> None:
    # Game days are 1, 2 and 4 (nobody played day 3); I played all three.
    history = h(_act(1), _act(2), _act(4), _act(4, actor=OTHER))
    assert ladders.longest_streak(history) == 3


def test_unbroken_breaks_on_a_day_others_played_without_me() -> None:
    history = h(_act(1), _act(2, actor=OTHER), _act(3))
    assert ladders.longest_streak(history) == 1


def test_hat_trick_streak_skips_games_i_hosted_and_resets_on_others() -> None:
    history = h(
        won(ME, 1),
        won(OTHER, 2, host=ME),  # my own game: skipped
        won(ME, 3),
        ev(E.GAME_UNSOLVED, None, HOST, game=4),  # resets
        won(ME, 5),
    )
    assert ladders.win_streak(history) == 2


def test_hat_trick_counts_a_refinished_game_by_its_final_outcome() -> None:
    history = h(
        won(ME, 1),
        ev(E.GAME_UNSOLVED, None, HOST, game=2),
        won(ME, 3),
        won(ME, 2, how="setwinner"),  # game 2 re-finished to my win
    )
    assert ladders.win_streak(history) == 3


def test_biggest_pot_is_the_largest_single_bounty_win() -> None:
    history = h(money(None, ME, 120, "bounty_win"), money(None, ME, 300, "bounty_win"))
    assert ladders.biggest_pot(history) == 300


def test_purist_counts_hard_mode_wins_without_my_clue() -> None:
    history = h(
        ev(E.CLUE_PURCHASED, ME, game=1, kind="tile"),
        won(ME, 1, hard_mode=True),
        won(ME, 2, hard_mode=True),
        won(ME, 3),
    )
    assert ladders.purist_wins(history) == 1


def test_warm_guesses_counts_only_new_reveals() -> None:
    assert ladders.warm_guesses(h(guess(1, reveal_new=True), guess(1, reveal_new=False))) == 1


def test_prompt_starts_counts_prompt_turn_bonuses() -> None:
    assert ladders.prompt_starts(h(money(None, ME, 10, "prompt_turn"))) == 1


def test_stumped_needs_three_guessers_and_a_normal_game() -> None:
    history = h(
        ev(E.GAME_UNSOLVED, None, ME, game=1, hard_mode=False, distinct_guessers=3),
        ev(E.GAME_UNSOLVED, None, ME, game=2, hard_mode=False, distinct_guessers=2),
    )
    assert ladders.stumped(history) == 1


def test_eagle_eye_and_hard_mode_hero() -> None:
    history = h(won(ME, 1, stage=1), won(ME, 2, stage=1, hard_mode=True), won(ME, 3))
    assert ladders.stage_one_wins(history) == 2
    assert ladders.hard_mode_wins(history) == 1


def test_ballots_counts_distinct_votes() -> None:
    history = h(ev(E.VOTE_COUNTED, ME, OTHER, game=1, won=True))
    assert ladders.ballots(history) == 1
