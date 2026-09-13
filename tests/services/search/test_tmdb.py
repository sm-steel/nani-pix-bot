import httpx
import pytest

from nani_pix_bot.services.search import tmdb


async def test_search_parses_a_result() -> None:
    entry = {
        "id": 209867,
        "name": "Frieren: Beyond Journey's End",
        "original_name": "葬送のフリーレン",
    }

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"results": [entry]})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        results = await tmdb.search(client, "frieren")

    assert results == [
        tmdb.TMDBResult(
            tmdb_id=209867,
            title_romaji=None,
            title_english="Frieren: Beyond Journey's End",
            title_native="葬送のフリーレン",
            synonyms=[],
        )
    ]


async def test_search_handles_missing_original_name() -> None:
    entry = {"id": 1, "name": "Some Anime", "original_name": None}

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"results": [entry]})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        results = await tmdb.search(client, "some anime")

    assert results == [
        tmdb.TMDBResult(
            tmdb_id=1,
            title_romaji=None,
            title_english="Some Anime",
            title_native=None,
            synonyms=[],
        )
    ]


async def test_search_limits_to_the_configured_result_count() -> None:
    entries = [{"id": i, "name": f"Anime {i}", "original_name": None} for i in range(10)]

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"results": entries})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        results = await tmdb.search(client, "anime", limit=5)

    assert len(results) == 5


async def test_search_retries_after_rate_limit_then_succeeds() -> None:
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        if calls["n"] == 1:
            return httpx.Response(429, headers={"Retry-After": "0"})
        return httpx.Response(200, json={"results": []})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        results = await tmdb.search(client, "frieren")

    assert results == []
    assert calls["n"] == 2


async def test_search_raises_after_exhausting_rate_limit_retries() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(429, headers={"Retry-After": "0"})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(RuntimeError, match="rate limit"):
            await tmdb.search(client, "frieren")


async def test_get_by_id_parses_the_result() -> None:
    entry = {
        "id": 209867,
        "name": "Frieren: Beyond Journey's End",
        "original_name": "葬送のフリーレン",
    }

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=entry)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        result = await tmdb.get_by_id(client, 209867)

    assert result == tmdb.TMDBResult(
        tmdb_id=209867,
        title_romaji=None,
        title_english="Frieren: Beyond Journey's End",
        title_native="葬送のフリーレン",
        synonyms=[],
    )


async def test_get_by_id_returns_none_when_tmdb_has_no_such_show() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(404, json={"status_message": "The resource you requested"})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        result = await tmdb.get_by_id(client, 999999)

    assert result is None
