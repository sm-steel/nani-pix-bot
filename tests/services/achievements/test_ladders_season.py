from nani_pix_bot.models.enums import CurrencyReason, Rarity
from nani_pix_bot.models.enums import EventType as E
from nani_pix_bot.services.achievements import catalogue, ladders
from tests.services.achievements.fake_history import FakeHistory, ev

ME, OTHER = 1, 2


def h(*events) -> FakeHistory:
    return FakeHistory(ME, list(events))


def _ended(season: int, actor: int = ME):
    return ev(E.SEASON_ENDED, actor, season_id=season, run_id="r", rank=1, xp=5)


def _completed(season: int, *, premium: bool = False, first: bool = False):
    return ev(E.TRACK_COMPLETED, ME, season_id=season, premium=premium, first=first)


def _premium(season: int, *, reversal: bool = False, actor: int = ME):
    return ev(
        E.CURRENCY_MOVED,
        actor,
        reason=CurrencyReason.PREMIUM.value,
        reversal=reversal,
        season_id=season,
        amount=150,
    )


def test_seasons_scored_counts_each_season_once_and_only_mine() -> None:
    history = h(_ended(1), _ended(1), _ended(2), _ended(3, OTHER))
    assert ladders.seasons_scored(history) == 2


def test_passes_completed_ignores_premium_and_duplicates() -> None:
    history = h(_completed(1), _completed(1), _completed(2, premium=True), _completed(3))
    assert ladders.passes_completed(history) == 2


def test_front_runs_need_first_on_the_free_track() -> None:
    history = h(
        _completed(1, first=True),
        _completed(2, first=False),
        _completed(3, premium=True, first=True),
    )
    assert ladders.front_runs(history) == 1


def test_season_cosmetics_counts_distinct_items() -> None:
    items = [ev(E.ITEM_GAINED, ME, item_id=i, source="pass", season_id=1) for i in "aab"]
    assert ladders.season_cosmetics(h(*items)) == 2


def test_premiums_bought_skips_reversals_other_reasons_and_repeats() -> None:
    history = h(
        _premium(1),
        _premium(1),
        _premium(2, reversal=True),
        _premium(3, actor=OTHER),
        ev(E.CURRENCY_MOVED, ME, reason="win", reversal=False, amount=3),
    )
    assert ladders.premiums_bought(history) == 1


def test_gated_genres_won_counts_distinct_tags_and_tolerates_old_events() -> None:
    history = h(
        ev(E.GAME_WON, ME, game=1, gate_tags=["Romance", "Action"]),
        ev(E.GAME_WON, ME, game=2, gate_tags=["Romance"]),
        ev(E.GAME_WON, ME, game=3),  # logged before gate_tags existed
        ev(E.GAME_WON, OTHER, game=4, gate_tags=["Drama"]),
    )
    assert ladders.gated_genres_won(history) == 2


_B, _S, _G, _P = Rarity.BRONZE, Rarity.SILVER, Rarity.GOLD, Rarity.PLATINUM
_LADDERS = {
    "season_veteran": ((1, 3, 5, 10), (_B, _S, _G, _P)),
    "season_completionist": ((1, 3, 5), (_B, _G, _P)),
    "season_front_runner": ((1, 3, 5), (_B, _G, _P)),
    "season_hoarder": ((5, 15, 30), (_B, _G, _P)),
    "season_patron": ((1, 3, 5), (_B, _G, _P)),
    "season_genre_explorer": ((2, 4, 6), (_B, _G, _P)),
}


def test_the_approved_ladder_table_is_pinned() -> None:
    actual = {k: (catalogue.get(k).tiers, catalogue.get(k).rarities) for k in _LADDERS}
    assert actual == _LADDERS
    assert [d.key for d in catalogue.CATALOGUE][-6:] == list(_LADDERS)
