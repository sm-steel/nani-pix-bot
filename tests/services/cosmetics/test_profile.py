from dataclasses import replace
from datetime import UTC, datetime, timedelta
from io import BytesIO
from types import MappingProxyType

import pytest
from PIL import Image

from nani_pix_bot.models.cosmetics import EquippedItem, OwnedItem
from nani_pix_bot.models.enums import CosmeticSlot, ItemSource, SeasonStatus
from nani_pix_bot.models.player import Player
from nani_pix_bot.models.season import SeasonResult, SeasonSchedule
from nani_pix_bot.seasons import registry
from nani_pix_bot.seasons.definition import Item
from nani_pix_bot.services.cards import backgrounds
from nani_pix_bot.services.cosmetics import looks, profile

ALICE = 2
BANNER_ID = "demo_1/profile_banner.sky"
NOW = datetime(2026, 1, 1, tzinfo=UTC)


def _png(color: tuple[int, int, int]) -> bytes:
    buffer = BytesIO()
    Image.new("RGB", (8, 8), color).save(buffer, format="PNG")
    return buffer.getvalue()


@pytest.fixture
def art(monkeypatch, tmp_path):
    """A generated sky banner for the demo run, and the demo run extended with it."""
    banners = tmp_path / "demo_1" / "banners"
    banners.mkdir(parents=True)
    (banners / "sky.png").write_bytes(_png((10, 20, 200)))
    monkeypatch.setattr(looks, "SEASON_ART_DIR", tmp_path)
    monkeypatch.setattr(backgrounds, "SEASON_ART_DIR", tmp_path)
    banner = Item(
        "profile_banner.sky",
        CosmeticSlot.PROFILE_BANNER,
        "sky.png",
        MappingProxyType({"EN": "Sky", "RU": "Sky"}),
    )
    demo = registry.all_runs()["demo_1"]
    runs = {"demo_1": replace(demo, items=(*demo.items, banner))}
    monkeypatch.setattr(registry, "all_runs", lambda: runs)
    registry.all_items.cache_clear()
    return tmp_path


@pytest.fixture
def alice(session):
    session.add(Player(telegram_user_id=ALICE, username="alice"))
    session.flush()
    return ALICE


def _ended_season(session, run_id: str, days: int, *, player: int | None = ALICE) -> int:
    row = SeasonSchedule(
        run_id=run_id,
        start_at=NOW + timedelta(days=days),
        end_at=NOW + timedelta(days=days + 5),
        status=SeasonStatus.ENDED,
        created_by=9,
    )
    session.add(row)
    session.flush()
    if player is not None:
        session.add(SeasonResult(season_id=row.id, player_id=player, rank=1, xp=10))
        session.flush()
    return row.id


def _equip_banner(session, player: int) -> None:
    session.add(OwnedItem(player_id=player, item_id=BANNER_ID, source=ItemSource.ADMIN))
    session.add(EquippedItem(player_id=player, slot=CosmeticSlot.PROFILE_BANNER, item_id=BANNER_ID))
    session.flush()


def test_build_with_two_seasons_and_an_equipped_banner(session, alice, art) -> None:
    _ended_season(session, "demo_1", 0)
    _ended_season(session, "demo_1", 30)
    _ended_season(session, "demo_1", 60, player=None)  # not played
    _equip_banner(session, alice)

    view = profile.build(session, alice, "EN")

    assert view.player_id == alice
    assert view.name == "@alice"
    assert view.title is None
    assert view.banner == (art / "demo_1" / "banners" / "sky.png").read_bytes()
    assert view.stamps == (
        profile.Stamp("demo_1", "Demo Season", gold=False),
        profile.Stamp("demo_1", "Demo Season", gold=False),
    )
    assert view.seasons_played == 2
    assert view.cosmetics_owned == 1


def test_stamps_are_localized(session, alice, art) -> None:
    _ended_season(session, "demo_1", 0)
    assert profile.build(session, alice, "RU").stamps[0].label == "Демо-сезон"


def test_banner_falls_back_to_the_latest_seasons_key_visual(session, alice, art) -> None:
    (art / "demo_1" / "key_visual.png").write_bytes(_png((200, 10, 10)))
    _ended_season(session, "demo_1", 0)
    view = profile.build(session, alice, "EN")
    assert view.banner == (art / "demo_1" / "key_visual.png").read_bytes()


def test_no_banner_and_no_seasons(session, alice, art) -> None:
    view = profile.build(session, alice, "EN")
    assert view.banner is None
    assert view.stamps == ()
    assert view.seasons_played == 0
    assert view.cosmetics_owned == 0


def test_title_text_is_resolved(session, alice, art) -> None:
    session.get(Player, alice).title_key = "item:demo_1/title.demo"
    session.flush()
    assert profile.build(session, alice, "EN").title == "Demo Star"
    assert profile.build(session, alice, "RU").title == "Демо-звезда"


def test_stats_are_seasons_with_a_medal_and_cosmetics_with_a_gift() -> None:
    assert [(e.icon, e.text) for e in profile.stats(2, 9, "EN")] == [
        ("medal", "Seasons: 2"),
        ("gift", "Cosmetics: 9"),
    ]
    assert [e.text for e in profile.stats(2, 9, "RU")] == ["Сезонов: 2", "Предметов: 9"]


def test_roster_is_every_player_by_name_ignoring_case_and_the_at(session) -> None:
    session.add_all(
        [
            Player(telegram_user_id=30, username="zed"),
            Player(telegram_user_id=10, username="Bob"),
            Player(telegram_user_id=20, first_name="amy"),
            Player(telegram_user_id=40),  # no name at all: shown and sorted by id
        ]
    )
    session.flush()
    assert profile.roster(session) == [40, 20, 10, 30]


def test_roster_breaks_name_ties_by_id(session) -> None:
    session.add_all(
        [Player(telegram_user_id=9, first_name="Kim"), Player(telegram_user_id=3, first_name="kim")]
    )
    session.flush()
    assert profile.roster(session) == [3, 9]


def test_roster_of_nobody_is_empty(session) -> None:
    assert profile.roster(session) == []


@pytest.mark.parametrize(
    ("current", "step", "expected"),
    [(2, 1, 3), (2, -1, 1), (3, 1, 1), (1, -1, 3)],
)
def test_neighbour_wraps_around(current: int, step: int, expected: int) -> None:
    assert profile.neighbour([1, 2, 3], current, step) == expected


def test_neighbour_of_someone_no_longer_listed_is_the_first() -> None:
    assert profile.neighbour([1, 2, 3], 99, 1) == 1


def test_neighbour_of_the_only_player_is_themselves() -> None:
    assert profile.neighbour([5], 5, 1) == 5
