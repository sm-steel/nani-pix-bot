from typing import Any

from nani_pix_bot.models.enums import EventType as E
from nani_pix_bot.services.achievements import conditions as c
from nani_pix_bot.services.events import LoggedEvent
from tests.services.achievements.fake_history import FakeHistory, ev

ME, OTHER = 1, 2


def h(*events: LoggedEvent) -> FakeHistory:
    return FakeHistory(ME, list(events))


def won(**data: Any) -> LoggedEvent:
    facts = {
        "stage": 3,
        "hard_mode": False,
        "how": "guess",
        "seconds": 600.0,
        "last_slot": False,
        "winner_wrong": 0,
        "first_guess": False,
        "distinct_guessers": 2,
    } | data
    return ev(E.GAME_WON, ME, 9, game=1, **facts)


def tip(amount: int) -> LoggedEvent:
    return ev(E.CURRENCY_MOVED, ME, OTHER, amount=amount, reason="tip", reversal=False)


def test_one_shot_wins() -> None:
    assert c.clutch(h(won(last_slot=True))) == 1
    assert c.clutch(h(won(last_slot=True, hard_mode=True))) == 0
    assert c.first_try(h(won(first_guess=True))) == 1
    assert c.first_try(h(won(first_guess=True, hard_mode=True))) == 0
    assert c.speed_demon(h(won(seconds=60.0))) == 1
    assert c.speed_demon(h(won(seconds=61.0))) == 0
    assert c.speed_demon(h(won(seconds=None))) == 0
    assert c.comeback(h(won(winner_wrong=3))) == 1
    assert c.comeback(h(won(winner_wrong=2))) == 0
    assert c.peoples_champion(h(won(how="vote"))) == 1
    assert c.peoples_champion(h(won(how="setwinner"))) == 0


def test_kingmaker() -> None:
    assert c.kingmaker(h(ev(E.VOTE_COUNTED, ME, OTHER, game=1, won=True))) == 1
    assert c.kingmaker(h(ev(E.VOTE_COUNTED, ME, OTHER, game=1, won=False))) == 0


def test_crowd_pleaser_counts_hosted_games_with_five_guessers() -> None:
    hosted = ev(E.GAME_UNSOLVED, None, ME, game=1, hard_mode=False, distinct_guessers=5)
    assert c.crowd_pleaser(h(hosted)) == 1


def test_shopaholic_needs_all_five_kinds_in_one_game() -> None:
    kinds = ["first_letter", "last_letter", "title_shape", "screenshot", "tile"]
    one_game = [ev(E.CLUE_PURCHASED, ME, game=1, kind=k) for k in kinds]
    split = [ev(E.CLUE_PURCHASED, ME, game=i, kind=k) for i, k in enumerate(kinds)]
    assert c.shopaholic(h(*one_game)) == 1
    assert c.shopaholic(h(*split)) == 0


def test_explorer_counts_distinct_sources_of_normal_games() -> None:
    hosted = [
        ev(E.GAME_ACTIVATED, ME, game=i, source=s, hard_mode=False)
        for i, s in enumerate(["anilist", "tmdb", "tmdb", "manual"])
    ]
    assert c.explorer(h(*hosted)) == 3


def test_hidden_ones() -> None:
    assert c.dethroned(h(ev(E.OVERTHROWN, ME))) == 1
    assert c.so_close(h(ev(E.GUESS, ME, game=1, correct=False, score=82.0))) == 1
    assert c.so_close(h(ev(E.GUESS, ME, game=1, correct=False, score=60.0))) == 0
    assert c.so_close(h(ev(E.GUESS, ME, game=1, correct=True, score=None))) == 0
    assert c.penny_pincher(h(tip(1))) == 1
    assert c.penny_pincher(h(tip(2))) == 0


def test_group_wins_counts_distinct_won_games_across_the_group() -> None:
    history = h(
        ev(E.GAME_WON, OTHER, game=1),
        ev(E.GAME_WON, ME, game=2),
        ev(E.GAME_WON, ME, game=2),  # refinish duplicate
    )
    assert c.group_wins(history) == 2
