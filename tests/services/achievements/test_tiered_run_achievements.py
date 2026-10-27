"""A run achievement with several tiers climbs like a ladder inside its season:
one grant per tier reached, each with its own rarity."""

from dataclasses import replace

import pytest
from sqlalchemy import select

from nani_pix_bot.commands.achievements import compare, render
from nani_pix_bot.models.achievement import AchievementGrant
from nani_pix_bot.models.enums import EventType, Rarity
from nani_pix_bot.seasons import registry
from nani_pix_bot.seasons.definition import RunAchievement
from nani_pix_bot.services.achievements import catalogue, status
from nani_pix_bot.services.achievements.definitions import Kind, rarity_of
from tests.seasons_fixtures import NOW, WINNER

pytestmark = pytest.mark.achievements

KEY = "demo_1.triple_win"
B, S, G = Rarity.BRONZE, Rarity.SILVER, Rarity.GOLD


def _wins(history) -> int:
    return len(history.mine(EventType.GAME_WON))


TIERED = RunAchievement(
    "triple_win",
    {"EN": "Triple Win", "RU": "Тройная победа"},
    {"EN": "Win season games", "RU": "Побеждайте в играх сезона"},
    B,
    frozenset({EventType.GAME_WON}),
    _wins,
    tiers=(1, 2, 3),
    rarities=(B, S, G),
)


@pytest.fixture(autouse=True)
def _tiered_run(fake_runs, monkeypatch):
    runs = {**fake_runs, "demo_1": replace(fake_runs["demo_1"], achievements=(TIERED,))}
    monkeypatch.setattr(registry, "all_runs", lambda: runs)
    monkeypatch.setattr(catalogue, "CATALOGUE", ())


def _tiers(session) -> list[int]:
    rows = session.scalars(select(AchievementGrant).where(AchievementGrant.key == KEY))
    return sorted(r.tier for r in rows if r.player_id == WINNER and r.period_key == "demo_1")


def test_it_builds_with_its_tiers_and_rarities() -> None:
    defn = catalogue.get(KEY)
    assert (defn.kind, defn.tiers, defn.rarities) == (Kind.SEASON, (1, 2, 3), (B, S, G))


def test_each_tier_is_granted_once_with_its_rarity(session, tagged_game, emit_win) -> None:
    defn = catalogue.get(KEY)
    for stage, held in ((1, [1]), (2, [1, 2]), (3, [1, 2, 3]), (4, [1, 2, 3])):
        emit_win(winner=WINNER, stage=stage)
        assert _tiers(session) == held
    assert [rarity_of(defn, t) for t in _tiers(session)] == [B, S, G]


def _status(session) -> status.Status:
    return next(s for s in status.build(session, WINNER, NOW) if s.defn.key == KEY)


def test_its_status_reads_like_a_ladder(session, tagged_game, emit_win) -> None:
    emit_win(winner=WINNER, stage=1)
    emit_win(winner=WINNER, stage=2)
    held = _status(session)
    assert (held.state, held.tier, held.value, held.target) == (status.State.EARNED, 2, 2, 3)
    line = render.row(held, "en", None)
    assert "🥈" in line
    assert "Triple Win II" in line
    assert "2/3" in line


def test_its_top_tier_has_no_next_target(session, tagged_game, emit_win) -> None:
    for stage in (1, 2, 3):
        emit_win(winner=WINNER, stage=stage)
    held = _status(session)
    assert (held.tier, held.target) == (3, None)
    assert "🥇" in render.row(held, "en", None)


def test_it_reads_like_a_ladder_after_its_season_closed(
    session, tagged_game, emit_win, end_season
) -> None:
    emit_win(winner=WINNER, stage=1)
    emit_win(winner=WINNER, stage=2)
    end_season()
    held = _status(session)
    assert (held.state, held.tier, held.value, held.target) == (status.State.EARNED, 2, 0, None)
    line = render.row(held, "en", None)
    assert "🥈" in line
    assert "Triple Win II" in line
    assert "/3" not in line


def test_sakura_builds_exactly_as_before() -> None:
    sakura = registry.discover(registry.RUNS_PACKAGE)["sakura_1"]
    built = catalogue._build_run_definitions({"sakura_1": sakura})
    assert [(d.key, d.kind, d.tiers, d.rarities, d.hidden) for d in built] == [
        ("sakura_1.kokuhaku", Kind.SEASON, (1,), (S,), False),
        ("sakura_1.hanami_party", Kind.SEASON, (1,), (B,), False),
        ("sakura_1.full_bloom", Kind.SEASON, (1,), (Rarity.PLATINUM,), True),
    ]
    assert all(d.run_id == "sakura_1" for d in built)


def test_compare_shows_its_tier(session, tagged_game, emit_win) -> None:
    emit_win(winner=WINNER, stage=1)
    emit_win(winner=WINNER, stage=2)
    assert compare._cell(_status(session)) == "✅ II"
