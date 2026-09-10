import httpx
import pytest

from nani_pix_bot.services import anilist


def _media_payload(entries: list[dict]) -> dict:
    return {"data": {"Page": {"media": entries}}}


async def test_search_parses_a_result() -> None:
    entry = {
        "id": 154587,
        "title": {
            "romaji": "Sousou no Frieren",
            "english": "Frieren: Beyond Journey's End",
            "native": "葬送のフリーレン",
        },
        "synonyms": ["Frieren", "Frieren at the Funeral"],
        "startDate": {"year": 2023},
    }

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=_media_payload([entry]))

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        results = await anilist.search(client, "frieren")

    assert results == [
        anilist.AniListResult(
            anilist_id=154587,
            title_romaji="Sousou no Frieren",
            title_english="Frieren: Beyond Journey's End",
            title_native="葬送のフリーレン",
            synonyms=["Frieren", "Frieren at the Funeral"],
            year=2023,
        )
    ]


async def test_search_handles_missing_synonyms_and_year() -> None:
    entry = {
        "id": 1,
        "title": {"romaji": "Some Anime", "english": None, "native": None},
        "synonyms": [],
        "startDate": {"year": None},
    }

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=_media_payload([entry]))

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        results = await anilist.search(client, "some anime")

    assert results == [
        anilist.AniListResult(
            anilist_id=1,
            title_romaji="Some Anime",
            title_english=None,
            title_native=None,
            synonyms=[],
            year=None,
        )
    ]


async def test_search_retries_after_rate_limit_then_succeeds() -> None:
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        if calls["n"] == 1:
            return httpx.Response(429, headers={"Retry-After": "0"})
        return httpx.Response(200, json=_media_payload([]))

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        results = await anilist.search(client, "frieren")

    assert results == []
    assert calls["n"] == 2


async def test_search_raises_after_exhausting_rate_limit_retries() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(429, headers={"Retry-After": "0"})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(RuntimeError, match="rate limit"):
            await anilist.search(client, "frieren")


async def test_search_sends_a_referer_header() -> None:
    # AniList (via Cloudflare) 403s requests with no Referer, regardless of
    # source IP/proxy — discovered against the real API after deploy.
    captured: dict[str, str | None] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["referer"] = request.headers.get("referer")
        return httpx.Response(200, json=_media_payload([]))

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        await anilist.search(client, "frieren")

    assert captured["referer"] == "https://anilist.co/"
