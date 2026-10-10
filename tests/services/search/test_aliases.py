from dataclasses import replace
from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest

from nani_pix_bot.models.enums import Provider
from nani_pix_bot.services.search import aliases, anilist, shikimori, tenrai
from nani_pix_bot.services.search.anilist import AniListResult
from nani_pix_bot.services.search.shikimori import ShikimoriResult
from nani_pix_bot.services.search.tenrai import TenraiResult

_RIGHT_QUOTE = chr(0x2019)  # typographic apostrophe

_CLIENTS = aliases.AliasClients(anilist=MagicMock(), shikimori=MagicMock(), tenrai=MagicMock())

_ANILIST = AniListResult(
    anilist_id=154587,
    title_romaji="Sousou no Frieren",
    title_english="Frieren: Beyond Journey's End",
    title_native="葬送のフリーレン",
    synonyms=["Frieren at the Funeral"],
    year=2023,
    mal_id=52991,
)
_SHIKIMORI = ShikimoriResult(
    shikimori_id=52991,
    title_romaji="Sousou no Frieren",
    title_english="Frieren: Beyond Journey's End",
    title_russian="Провожающая в последний путь Фрирен",
    synonyms=["Frieren"],
)
_TENRAI = TenraiResult(
    tenrai_id=52991,
    title_romaji="Sousou no Frieren",
    title_english="Frieren: Beyond Journey's End",
    title_native="葬送のフリーレン",
    synonyms=[f"Frieren: Beyond Journey{_RIGHT_QUOTE}s End", "Sousou no Furiiren"],
)


_SHIKIMORI_PICK = aliases.AliasLookup(
    source=Provider.SHIKIMORI.value,
    anilist_id=None,
    shikimori_id=52991,
    tenrai_id=None,
    title="Sousou no Frieren",
    accepted=("Sousou no Frieren", "Frieren"),
)


def _lookup(**overrides) -> aliases.AliasLookup:
    return replace(_SHIKIMORI_PICK, **overrides)


@pytest.fixture
def providers(monkeypatch: pytest.MonkeyPatch) -> dict[str, AsyncMock]:
    mocks = {
        "anilist_by_mal": AsyncMock(return_value=_ANILIST),
        "anilist_by_id": AsyncMock(return_value=_ANILIST),
        "shikimori_by_id": AsyncMock(return_value=_SHIKIMORI),
        "tenrai_by_id": AsyncMock(return_value=_TENRAI),
        "tenrai_search": AsyncMock(return_value=[_TENRAI]),
    }
    monkeypatch.setattr(anilist, "get_by_mal_id", mocks["anilist_by_mal"])
    monkeypatch.setattr(anilist, "get_by_id", mocks["anilist_by_id"])
    monkeypatch.setattr(shikimori, "get_by_id", mocks["shikimori_by_id"])
    monkeypatch.setattr(tenrai, "get_by_id", mocks["tenrai_by_id"])
    monkeypatch.setattr(tenrai, "search", mocks["tenrai_search"])
    return mocks


def test_merge_drops_accepted_names_and_duplicates_the_way_matching_compares() -> None:
    merged = aliases.merge_suggestions(
        ["Sousou no Frieren"],
        [
            "sousou no frieren!",
            "Frieren",
            " Frieren ",
            "Journey's End",
            f"Journey{_RIGHT_QUOTE}s End",
            "  ",
        ],
    )
    assert merged == ["Frieren", "Journey's End"]


def test_merge_keeps_numbered_names_apart_and_caps_the_list() -> None:
    assert aliases.merge_suggestions(["Overlord"], ["Overlord II"]) == ["Overlord II"]
    assert len(aliases.merge_suggestions([], [f"name {i}" for i in range(30)])) == (
        aliases.MAX_SUGGESTIONS
    )


async def test_a_shikimori_pick_is_linked_by_its_own_id_to_the_other_two(providers) -> None:
    found = await aliases.find_aliases(_CLIENTS, _lookup())

    providers["anilist_by_mal"].assert_awaited_once_with(_CLIENTS.anilist, 52991)
    providers["tenrai_by_id"].assert_awaited_once_with(_CLIENTS.tenrai, 52991)
    providers["shikimori_by_id"].assert_not_awaited()
    assert found == [
        "Frieren: Beyond Journey's End",
        "葬送のフリーレン",
        "Frieren at the Funeral",
        "Sousou no Furiiren",
    ]


async def test_an_anilist_pick_links_through_its_mal_id(providers) -> None:
    await aliases.find_aliases(
        _CLIENTS, _lookup(source=Provider.ANILIST.value, shikimori_id=None, anilist_id=154587)
    )

    providers["shikimori_by_id"].assert_awaited_once_with(_CLIENTS.shikimori, 52991)
    providers["anilist_by_mal"].assert_not_awaited()


async def test_a_manual_pick_links_through_a_matching_tenrai_title(providers) -> None:
    found = await aliases.find_aliases(
        _CLIENTS, _lookup(source="manual", shikimori_id=None, title="Sousou no Frieren")
    )

    providers["tenrai_search"].assert_awaited_once()
    assert "Провожающая в последний путь Фрирен" in found


async def test_a_title_search_never_takes_a_different_season(providers) -> None:
    found = await aliases.find_aliases(
        _CLIENTS, _lookup(source="manual", shikimori_id=None, title="Sousou no Frieren 2nd Season")
    )

    assert found == []
    providers["anilist_by_mal"].assert_not_awaited()


async def test_one_provider_failing_keeps_the_others(providers, log_records) -> None:
    providers["anilist_by_mal"].side_effect = httpx.ConnectError("down")

    found = await aliases.find_aliases(_CLIENTS, _lookup())

    assert "Sousou no Furiiren" in found
    assert any(line.level == "ERROR" and "AniList" in line.message for line in log_records)


async def test_nothing_to_link_by_finds_nothing(providers) -> None:
    providers["tenrai_search"].return_value = []

    assert await aliases.find_aliases(_CLIENTS, _lookup(source="manual", shikimori_id=None)) == []
