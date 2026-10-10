from datetime import UTC, datetime
from types import MappingProxyType
from unittest.mock import MagicMock

from nani_pix_bot.models.enums import SeasonStatus
from nani_pix_bot.models.season import SeasonSchedule
from nani_pix_bot.seasons.definition import Gate, GateTag
from nani_pix_bot.services.search.tags import AnimeTag
from nani_pix_bot.services.seasons import gate as gate_service
from nani_pix_bot.services.seasons.gate import Verdict
from nani_pix_bot.services.seasons.tags import TagClients, TagsUnavailableError

SAKURA_LIKE = Gate(
    any_of=(
        GateTag("genre", "Slice of Life", 36, 36),
        GateTag("genre", "Romance", 22, 22),
        GateTag("theme", "School", 23, 23),
    ),
    description=MappingProxyType({"EN": "Slice of Life, Romance, or set in a school"}),
)
K_ON = [AnimeTag("genre", "Comedy", 4), AnimeTag("theme", "School", 23)]
GURREN = [AnimeTag("genre", "Sci-Fi", 24), AnimeTag("theme", "Mecha", 18)]
CLIENTS = TagClients(shikimori=MagicMock(), tenrai=MagicMock())
NOW = datetime(2026, 11, 1, 12, 0, tzinfo=UTC)


def test_matches_by_mal_id_including_themes() -> None:
    assert gate_service.matches(SAKURA_LIKE, K_ON)
    assert not gate_service.matches(SAKURA_LIKE, GURREN)


def test_shikimori_tags_match_by_kind_and_name_only() -> None:
    assert gate_service.matches(SAKURA_LIKE, [AnimeTag("theme", "school", None)])
    assert not gate_service.matches(SAKURA_LIKE, [AnimeTag("genre", "School", None)])  # wrong kind
    assert not gate_service.matches(SAKURA_LIKE, [AnimeTag("theme", "Award Winning", 114)])


async def test_check_uses_known_tags_without_fetching(monkeypatch) -> None:
    async def never(*_a):
        raise AssertionError("fetched")

    monkeypatch.setattr(gate_service, "fetch_tags", never)
    result = await gate_service.check(SAKURA_LIKE, mal_id=None, clients=CLIENTS, known=K_ON)
    assert result.verdict is Verdict.PASSED


async def test_no_mal_id_is_unidentifiable() -> None:
    result = await gate_service.check(SAKURA_LIKE, mal_id=None, clients=CLIENTS, known=None)
    assert result.verdict is Verdict.UNIDENTIFIABLE


async def test_unknown_anime_is_unidentifiable(monkeypatch) -> None:
    async def missing(*_a):
        return None

    monkeypatch.setattr(gate_service, "fetch_tags", missing)
    result = await gate_service.check(SAKURA_LIKE, mal_id=1, clients=CLIENTS, known=None)
    assert result.verdict is Verdict.UNIDENTIFIABLE


async def test_provider_outage_is_unavailable_not_off_theme(monkeypatch) -> None:
    async def down(*_a):
        raise TagsUnavailableError

    monkeypatch.setattr(gate_service, "fetch_tags", down)
    result = await gate_service.check(SAKURA_LIKE, mal_id=4224, clients=CLIENTS, known=None)
    assert result.verdict is Verdict.UNAVAILABLE


async def test_fetched_off_theme_tags_are_returned_for_storage(monkeypatch) -> None:
    async def gurren(*_a):
        return GURREN

    monkeypatch.setattr(gate_service, "fetch_tags", gurren)
    result = await gate_service.check(SAKURA_LIKE, mal_id=2001, clients=CLIENTS, known=None)
    assert result.verdict is Verdict.OFF_THEME
    assert result.tags == tuple(GURREN)


def _season(session, status: SeasonStatus) -> None:
    session.add(
        SeasonSchedule(run_id="demo_1", start_at=NOW, end_at=NOW, status=status, created_by=7)
    )
    session.flush()


def test_active_gate_is_the_running_seasons_gate(session) -> None:
    assert gate_service.active_gate(session) is None
    _season(session, SeasonStatus.SCHEDULED)
    assert gate_service.active_gate(session) is None


def test_active_gate_of_an_active_season(session, fake_runs) -> None:
    _season(session, SeasonStatus.ACTIVE)
    assert gate_service.active_gate(session) == fake_runs["demo_1"].gate
