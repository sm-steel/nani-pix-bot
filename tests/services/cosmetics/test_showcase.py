from datetime import UTC, datetime, timedelta

import pytest

from nani_pix_bot.models.achievement import AchievementGrant
from nani_pix_bot.models.cosmetics import ShowcasePick
from nani_pix_bot.models.enums import Rarity, SeasonStatus, ShowcaseKind
from nani_pix_bot.models.player import Player
from nani_pix_bot.models.season import SeasonResult, SeasonSchedule
from nani_pix_bot.services.cosmetics import showcase
from nani_pix_bot.services.cosmetics.showcase import Toggle

ME = 2
NOW = datetime(2026, 1, 1, tzinfo=UTC)
STAMP, ACH = ShowcaseKind.STAMP, ShowcaseKind.ACHIEVEMENT


@pytest.fixture(autouse=True)
def me(session) -> int:
    session.add(Player(telegram_user_id=ME, username="me"))
    session.flush()
    return ME


def _season(session, run_id: str, days: int, *, played: bool = True) -> None:
    row = SeasonSchedule(
        run_id=run_id,
        start_at=NOW + timedelta(days=days),
        end_at=NOW + timedelta(days=days + 5),
        status=SeasonStatus.ENDED,
        created_by=9,
    )
    session.add(row)
    session.flush()
    if played:
        session.add(SeasonResult(season_id=row.id, player_id=ME, rank=1, xp=10))
        session.flush()


def _grant(session, key: str, tier: int, rarity: Rarity, days: int = 0, period: str = "") -> int:
    row = AchievementGrant(
        player_id=ME,
        key=key,
        tier=tier,
        period_key=period,
        rarity=rarity,
        reward=0,
        points=0,
        granted_at=NOW + timedelta(days=days),
    )
    session.add(row)
    session.flush()
    return row.id


def _seasons(session, count: int) -> list[str]:
    """`count` played seasons, run ids s0.. oldest first; returns them newest first."""
    for i in range(count):
        _season(session, f"s{i}", days=10 * i)
    return [f"s{i}" for i in reversed(range(count))]


# --- choices -------------------------------------------------------------------


def test_stamp_choices_are_the_seasons_played_newest_first(session) -> None:
    newest_first = _seasons(session, 3)
    _season(session, "skipped", days=100, played=False)
    assert showcase.stamp_choices(session, ME) == newest_first


def test_badge_choices_keep_only_the_highest_tier_of_each_achievement(session) -> None:
    _grant(session, "sharpshooter", 1, Rarity.BRONZE)
    top = _grant(session, "sharpshooter", 2, Rarity.SILVER, days=1)
    once = _grant(session, "hat_trick", 1, Rarity.GOLD)
    assert sorted(h.grant_id for h in showcase.badge_choices(session, ME)) == sorted([top, once])


def test_each_champion_period_is_its_own_choice(session) -> None:
    week_41 = _grant(session, "champion_week", 1, Rarity.GOLD, period="2026-W41")
    week_42 = _grant(session, "champion_week", 1, Rarity.GOLD, days=7, period="2026-W42")
    ids = {h.grant_id for h in showcase.badge_choices(session, ME)}
    assert ids == {week_41, week_42}


def test_badge_choices_are_rarest_first_then_tier_then_newest(session) -> None:
    gold_old = _grant(session, "a", 1, Rarity.GOLD, days=0)
    gold_new = _grant(session, "b", 1, Rarity.GOLD, days=5)
    gold_tier2 = _grant(session, "c", 2, Rarity.GOLD, days=0)
    platinum = _grant(session, "d", 1, Rarity.PLATINUM)
    bronze = _grant(session, "e", 3, Rarity.BRONZE)
    order = [h.grant_id for h in showcase.badge_choices(session, ME)]
    assert order == [platinum, gold_tier2, gold_new, gold_old, bronze]


# --- defaults ------------------------------------------------------------------


def test_without_picks_the_newest_six_stamps_show(session) -> None:
    newest_first = _seasons(session, 8)
    assert showcase.shown_stamps(session, ME) == newest_first[: showcase.STAMPS_MAX]


def test_without_picks_the_four_rarest_achievements_show(session) -> None:
    ids = [_grant(session, f"k{i}", 1, rarity) for i, rarity in enumerate(Rarity)]
    extra = _grant(session, "k9", 1, Rarity.BRONZE, days=9)
    shown = [h.grant_id for h in showcase.shown_badges(session, ME)]
    assert shown == [ids[3], ids[2], ids[1], extra]


def test_nothing_held_shows_nothing(session) -> None:
    assert showcase.shown_stamps(session, ME) == []
    assert showcase.shown_badges(session, ME) == []


# --- toggling ------------------------------------------------------------------


def test_picks_show_in_the_order_they_were_ticked(session) -> None:
    _seasons(session, 3)
    assert showcase.toggle(session, ME, STAMP, "s0") is Toggle.ADDED
    assert showcase.toggle(session, ME, STAMP, "s2") is Toggle.ADDED
    assert showcase.shown_stamps(session, ME) == ["s0", "s2"]


def test_ticking_again_removes_the_pick(session) -> None:
    _seasons(session, 2)
    showcase.toggle(session, ME, STAMP, "s0")
    showcase.toggle(session, ME, STAMP, "s1")
    assert showcase.toggle(session, ME, STAMP, "s0") is Toggle.REMOVED
    assert showcase.shown_stamps(session, ME) == ["s1"]


def test_a_re_ticked_pick_goes_to_the_end(session) -> None:
    _seasons(session, 2)
    showcase.toggle(session, ME, STAMP, "s0")
    showcase.toggle(session, ME, STAMP, "s1")
    showcase.toggle(session, ME, STAMP, "s0")
    showcase.toggle(session, ME, STAMP, "s0")
    assert showcase.shown_stamps(session, ME) == ["s1", "s0"]


def test_ticking_past_the_limit_is_refused(session) -> None:
    ids = [str(_grant(session, f"k{i}", 1, Rarity.GOLD)) for i in range(showcase.BADGES_MAX + 1)]
    for ref in ids[:-1]:
        assert showcase.toggle(session, ME, ACH, ref) is Toggle.ADDED
    assert showcase.toggle(session, ME, ACH, ids[-1]) is Toggle.FULL
    assert len(showcase.picks(session, ME, ACH)) == showcase.BADGES_MAX


def test_ticking_something_not_held_is_refused(session) -> None:
    other = Player(telegram_user_id=77)
    session.add(other)
    session.flush()
    stranger = AchievementGrant(
        player_id=77, key="x", tier=1, period_key="", rarity=Rarity.GOLD, reward=0, points=0
    )
    session.add(stranger)
    session.flush()
    assert showcase.toggle(session, ME, ACH, str(stranger.id)) is Toggle.UNKNOWN
    assert showcase.toggle(session, ME, STAMP, "never_played") is Toggle.UNKNOWN
    assert showcase.toggle(session, ME, ACH, "not-a-number") is Toggle.UNKNOWN


def test_the_limits_match_the_card() -> None:
    assert (showcase.STAMPS_MAX, showcase.BADGES_MAX) == (6, 4)


# --- stale picks -----------------------------------------------------------------


def test_a_pick_that_no_longer_resolves_is_skipped(session) -> None:
    gone = _grant(session, "a", 1, Rarity.GOLD)
    kept = _grant(session, "b", 1, Rarity.SILVER)
    showcase.toggle(session, ME, ACH, str(gone))
    showcase.toggle(session, ME, ACH, str(kept))
    session.delete(session.get(AchievementGrant, gone))
    session.flush()
    assert [h.grant_id for h in showcase.shown_badges(session, ME)] == [kept]


def test_when_no_pick_resolves_the_defaults_come_back(session) -> None:
    newest_first = _seasons(session, 2)
    session.add(ShowcasePick(player_id=ME, kind=STAMP, ref="vanished", position=1))
    session.flush()
    assert showcase.shown_stamps(session, ME) == newest_first
