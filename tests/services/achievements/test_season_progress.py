from dataclasses import dataclass, field, replace
from datetime import UTC, date, datetime

from nani_pix_bot.models.enums import CurrencyReason, EventType
from nani_pix_bot.seasons import registry
from nani_pix_bot.seasons.definition import SeasonRun
from nani_pix_bot.services.achievements import season_progress as sp
from tests.services.achievements.fake_history import FakeHistory, ev

E = EventType
ME = 1
FIRST, MID, LAST = date(2026, 10, 5), date(2026, 10, 8), date(2026, 10, 12)


def _run(run_id: str = "demo_1") -> SeasonRun:
    return registry.discover("tests.seasons.fake_runs")[run_id]


@dataclass
class FakeSeasonHistory(FakeHistory):
    season_id: int = 1
    run: SeasonRun = field(default_factory=_run)
    first_day: date = FIRST
    last_day: date = LAST


def _hist(*events, **kwargs) -> FakeSeasonHistory:
    return FakeSeasonHistory(ME, list(events), **kwargs)


def _at(day: date) -> datetime:
    return datetime(day.year, day.month, day.day, 12, tzinfo=UTC)


def test_debut() -> None:
    assert sp.debut(_hist()) == 0
    assert sp.debut(_hist(ev(E.GUESS, ME, at=_at(MID)))) == 1
    assert sp.debut(_hist(ev(E.GAME_ACTIVATED, ME, at=_at(MID)))) == 1


def test_a_hard_mode_hosting_is_not_a_debut() -> None:
    assert sp.debut(_hist(ev(E.GAME_ACTIVATED, ME, hard_mode=True, at=_at(MID)))) == 0


def test_opening_act_needs_play_on_the_first_day() -> None:
    assert sp.opening_act(_hist(ev(E.GUESS, ME, at=_at(FIRST)))) == 1
    assert sp.opening_act(_hist(ev(E.GUESS, ME, at=_at(MID)))) == 0


def test_curtain_call_needs_play_on_the_last_day() -> None:
    assert sp.curtain_call(_hist(ev(E.GUESS, ME, at=_at(LAST)))) == 1
    assert sp.curtain_call(_hist(ev(E.GUESS, ME, at=_at(MID)))) == 0


def test_the_pass_ones_split_on_the_premium_flag() -> None:
    free = ev(E.TRACK_COMPLETED, ME, season_id=1, premium=False, first=True)
    paid = ev(E.TRACK_COMPLETED, ME, season_id=1, premium=True, first=False)
    assert (sp.pass_complete(_hist(free)), sp.pass_premium(_hist(free))) == (1, 0)
    assert (sp.pass_complete(_hist(paid)), sp.pass_premium(_hist(paid))) == (0, 1)


def test_another_seasons_pass_is_ignored() -> None:
    other = ev(E.TRACK_COMPLETED, ME, season_id=2, premium=True, first=False)
    assert sp.pass_complete(_hist(other)) == 0
    assert sp.pass_premium(_hist(other)) == 0


def _spent(reason: CurrencyReason, season_id: int):
    return ev(E.CURRENCY_MOVED, ME, reason=reason.value, season_id=season_id)


def test_window_shopper_wants_a_shop_purchase_in_this_season() -> None:
    assert sp.window_shopper(_hist(_spent(CurrencyReason.SEASON_SHOP, 1))) == 1
    assert sp.window_shopper(_hist(_spent(CurrencyReason.GRANT, 1))) == 0
    assert sp.window_shopper(_hist(_spent(CurrencyReason.SEASON_SHOP, 2))) == 0


def _gained(*keys: str):
    return [ev(E.ITEM_GAINED, ME, item_id=f"demo_1/{k}", season_id=1) for k in keys]


def test_collector_needs_every_shop_item() -> None:
    assert sp.collector(_hist(*_gained("name_flair.dango"))) == 0
    assert sp.collector(_hist(*_gained("name_flair.dango", "host_frame.border"))) == 1


def test_collector_is_zero_for_a_run_with_nothing_to_collect() -> None:
    assert sp.collector(_hist(*_gained("name_flair.dango"), run=_run("demo_2"))) == 0


def _ended(rank: int, season_id: int = 1):
    return ev(E.SEASON_ENDED, ME, season_id=season_id, run_id="demo_1", rank=rank, xp=10)


def test_each_podium_wants_its_exact_rank() -> None:
    for place in (1, 2, 3):
        got = [sp.podium(p)(_hist(_ended(place))) for p in (1, 2, 3)]
        assert got == [int(p == place) for p in (1, 2, 3)]


def test_another_seasons_rank_is_ignored() -> None:
    assert sp.podium(1)(_hist(_ended(1, season_id=2))) == 0


def test_on_theme_counts_wins_in_a_gated_run() -> None:
    wins = [ev(E.GAME_WON, ME) for _ in range(3)]
    assert sp.on_theme(_hist(*wins)) == 3


def test_on_theme_is_zero_in_an_ungated_run() -> None:
    ungated = replace(_run(), gate=None)
    assert sp.on_theme(_hist(ev(E.GAME_WON, ME), run=ungated)) == 0
