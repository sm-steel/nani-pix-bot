"""The bot's own random picks honour the active season's anime gate (#386)."""

from types import MappingProxyType

import pytest

from nani_pix_bot.models.enums import Provider
from nani_pix_bot.seasons.definition import Gate, GateTag
from nani_pix_bot.services.game import autostart
from nani_pix_bot.services.search.shikimori import ShikimoriResult
from nani_pix_bot.services.search.tags import AnimeTag
from nani_pix_bot.services.search.tenrai import TenraiResult
from nani_pix_bot.services.seasons.tags import TagsUnavailableError
from tests.conftest import LogLine
from tests.services.game.test_autostart import _StubAsyncClient

ROMANCE = GateTag("genre", "Romance", 22, 22)
SCHOOL = GateTag("theme", "School", 23, 23)
NO_SHIKI_ID = GateTag("theme", "Award Winning", 114, None)
SAKURA_LIKE = Gate(
    any_of=(GateTag("genre", "Slice of Life", 36, 36), ROMANCE, SCHOOL),
    description=MappingProxyType({"EN": "Slice of Life, Romance, or school"}),
)
NO_SHIKI_GATE = Gate(any_of=(NO_SHIKI_ID,), description=MappingProxyType({"EN": "x"}))

SHIKI = ShikimoriResult(
    shikimori_id=1, title_romaji="Clannad", title_english=None, title_russian=None, synonyms=[]
)
TENRAI = TenraiResult(
    tenrai_id=2, title_romaji="K-On", title_english=None, title_native=None, synonyms=[]
)
ON_THEME = [AnimeTag("theme", "Award Winning", 114)]
OFF_THEME = [AnimeTag("theme", "Mecha", 18)]


@pytest.fixture
def calls(monkeypatch: pytest.MonkeyPatch) -> dict:
    """Records shikimori.random_anime kwargs and tenrai/tag calls; the
    screenshot step always succeeds, so only the anime pick is under test.
    Tests tweak `shiki_result` / `tags_result` to steer the fakes."""
    record: dict = {
        "shiki": [],
        "tenrai": 0,
        "tags": [],
        "shiki_result": SHIKI,
        "tags_result": ON_THEME,
    }

    async def fake_screenshot(clients, anime):
        return "shot"

    async def fake_shiki(client, **kwargs):
        record["shiki"].append(kwargs)
        return record["shiki_result"]

    async def fake_tenrai(client):
        record["tenrai"] += 1
        return TENRAI

    async def fake_fetch_tags(clients, mal_id):
        record["tags"].append(mal_id)
        if isinstance(record["tags_result"], Exception):
            raise record["tags_result"]
        return record["tags_result"]

    monkeypatch.setattr(autostart, "_pick_screenshot", fake_screenshot)
    monkeypatch.setattr("nani_pix_bot.services.search.shikimori.random_anime", fake_shiki)
    monkeypatch.setattr("nani_pix_bot.services.search.tenrai.random_anime", fake_tenrai)
    monkeypatch.setattr(autostart, "fetch_tags", fake_fetch_tags)
    return record


async def _gather(gate: Gate | None):
    return await autostart.gather_pick(
        _StubAsyncClient(), _StubAsyncClient(), _StubAsyncClient(), gate=gate
    )


async def test_gated_pick_filters_shikimori_on_the_chosen_tags_id(
    monkeypatch: pytest.MonkeyPatch, calls: dict
) -> None:
    monkeypatch.setattr(autostart.secrets, "choice", lambda _seq: ROMANCE)

    pick = await _gather(SAKURA_LIKE)

    assert pick is not None
    assert pick.anime.source == Provider.SHIKIMORI
    assert calls["shiki"] == [{"genre_id": 22}]
    assert calls["tags"] == []  # a filtered Shikimori pick is not re-checked


async def test_tag_without_a_shikimori_id_goes_straight_to_tenrai(
    monkeypatch: pytest.MonkeyPatch, calls: dict
) -> None:
    monkeypatch.setattr(autostart.secrets, "choice", lambda _seq: NO_SHIKI_ID)

    pick = await _gather(NO_SHIKI_GATE)

    assert pick is not None
    assert pick.anime.source == Provider.TENRAI
    assert calls["shiki"] == []
    assert calls["tags"] == [2]


async def test_off_theme_tenrai_pick_fails_every_attempt(
    monkeypatch: pytest.MonkeyPatch, calls: dict, log_records: list[LogLine]
) -> None:
    monkeypatch.setattr(autostart.secrets, "choice", lambda _seq: NO_SHIKI_ID)
    calls["tags_result"] = OFF_THEME

    assert await _gather(NO_SHIKI_GATE) is None

    assert calls["tenrai"] == autostart.AUTOSTART_ATTEMPT_LIMIT
    assert any("off this season's theme" in r.message for r in log_records)
    assert any("exhausted" in r.message for r in log_records)


async def test_tenrai_fallback_after_empty_shikimori_is_gated(
    monkeypatch: pytest.MonkeyPatch, calls: dict
) -> None:
    monkeypatch.setattr(autostart.secrets, "choice", lambda _seq: SCHOOL)
    calls["shiki_result"] = None
    calls["tags_result"] = [AnimeTag("theme", "School", 23)]

    pick = await _gather(SAKURA_LIKE)

    assert pick is not None
    assert pick.anime.source == Provider.TENRAI
    assert calls["shiki"] == [{"genre_id": 23}]
    assert calls["tags"] == [2]


async def test_unavailable_tags_reject_the_attempt(
    monkeypatch: pytest.MonkeyPatch, calls: dict
) -> None:
    monkeypatch.setattr(autostart.secrets, "choice", lambda _seq: NO_SHIKI_ID)
    calls["tags_result"] = TagsUnavailableError()

    assert await _gather(NO_SHIKI_GATE) is None
    assert len(calls["tags"]) == autostart.AUTOSTART_ATTEMPT_LIMIT


async def test_tenrai_pick_with_no_tags_is_rejected(
    monkeypatch: pytest.MonkeyPatch, calls: dict
) -> None:
    monkeypatch.setattr(autostart.secrets, "choice", lambda _seq: NO_SHIKI_ID)
    calls["tags_result"] = None

    assert await _gather(NO_SHIKI_GATE) is None


async def test_without_a_gate_nothing_is_filtered_or_checked(calls: dict) -> None:
    pick = await _gather(None)

    assert pick is not None
    assert calls["shiki"] == [{"genre_id": None}]
    assert calls["tags"] == []


async def test_gated_pick_lines_carry_the_season(
    monkeypatch: pytest.MonkeyPatch, calls: dict, log_records: list[LogLine], session
) -> None:
    # The season is bound where the gate is resolved (active_gate), the
    # way run_bot_autostart does it just before gather_pick.
    from datetime import UTC, datetime

    from nani_pix_bot.models.enums import SeasonStatus
    from nani_pix_bot.models.season import SeasonSchedule
    from nani_pix_bot.seasons import registry
    from nani_pix_bot.services.seasons import gate as gate_service

    runs = registry.discover("tests.seasons.fake_runs")
    monkeypatch.setattr(registry, "all_runs", lambda: runs)
    now = datetime(2026, 11, 1, tzinfo=UTC)
    row = SeasonSchedule(
        run_id="demo_1", start_at=now, end_at=now, status=SeasonStatus.ACTIVE, created_by=7
    )
    session.add(row)
    session.flush()
    assert gate_service.active_gate(session) is not None

    monkeypatch.setattr(autostart.secrets, "choice", lambda _seq: NO_SHIKI_ID)
    calls["tags_result"] = OFF_THEME
    assert await _gather(NO_SHIKI_GATE) is None

    for line in (r for r in log_records if "off this season's theme" in r.message):
        assert line.extra["season_id"] == row.id
        assert line.extra["run_id"] == "demo_1"
    assert any("off this season's theme" in r.message for r in log_records)
