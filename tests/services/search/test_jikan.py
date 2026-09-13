import httpx
import pytest

from nani_pix_bot.services.search import cache, jikan


@pytest.fixture(autouse=True)
def _clear_cache():
    cache.clear()
    yield
    cache.clear()


async def test_search_parses_a_result() -> None:
    entry = {
        "mal_id": 52991,
        "title": "Sousou no Frieren",
        "title_english": "Frieren: Beyond Journey's End",
        "title_japanese": "葬送のフリーレン",
        "title_synonyms": ["Frieren at the Funeral"],
    }

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"data": [entry]})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        results = await jikan.search(client, "frieren")

    assert results == [
        jikan.JikanResult(
            jikan_id=52991,
            title_romaji="Sousou no Frieren",
            title_english="Frieren: Beyond Journey's End",
            title_native="葬送のフリーレン",
            synonyms=["Frieren at the Funeral"],
        )
    ]


async def test_search_handles_missing_english_title_and_synonyms() -> None:
    entry = {"mal_id": 1, "title": "Some Anime", "title_english": None, "title_japanese": None}

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"data": [entry]})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        results = await jikan.search(client, "some anime")

    assert results == [
        jikan.JikanResult(
            jikan_id=1,
            title_romaji="Some Anime",
            title_english=None,
            title_native=None,
            synonyms=[],
        )
    ]


async def test_search_sends_a_descriptive_user_agent() -> None:
    captured: dict[str, str | None] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["user_agent"] = request.headers.get("user-agent")
        return httpx.Response(200, json={"data": []})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        await jikan.search(client, "frieren")

    assert captured["user_agent"] == jikan._REQUEST_HEADERS["User-Agent"]


async def test_search_retries_after_rate_limit_then_succeeds() -> None:
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        if calls["n"] == 1:
            return httpx.Response(429, headers={"Retry-After": "0"})
        return httpx.Response(200, json={"data": []})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        results = await jikan.search(client, "frieren")

    assert results == []
    assert calls["n"] == 2


async def test_search_raises_after_exhausting_rate_limit_retries() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(429, headers={"Retry-After": "0"})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(RuntimeError, match="rate limit"):
            await jikan.search(client, "frieren")


async def test_get_by_id_parses_the_result() -> None:
    entry = {
        "mal_id": 52991,
        "title": "Sousou no Frieren",
        "title_english": "Frieren: Beyond Journey's End",
        "title_japanese": "葬送のフリーレン",
        "title_synonyms": ["Frieren at the Funeral"],
    }

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"data": entry})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        result = await jikan.get_by_id(client, 52991)

    assert result == jikan.JikanResult(
        jikan_id=52991,
        title_romaji="Sousou no Frieren",
        title_english="Frieren: Beyond Journey's End",
        title_native="葬送のフリーレン",
        synonyms=["Frieren at the Funeral"],
    )


async def test_get_by_id_returns_none_when_jikan_has_no_such_anime() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(404, json={"status": 404, "message": "Resource not found"})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        result = await jikan.get_by_id(client, 999999)

    assert result is None


async def test_search_is_cached_for_repeated_identical_queries() -> None:
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        return httpx.Response(200, json={"data": []})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        await jikan.search(client, "frieren")
        await jikan.search(client, "frieren")

    assert calls["n"] == 1


async def test_get_by_id_is_cached_for_repeated_identical_ids() -> None:
    entry = {"mal_id": 52991, "title": "Sousou no Frieren", "title_english": None}
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        return httpx.Response(200, json={"data": entry})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        await jikan.get_by_id(client, 52991)
        await jikan.get_by_id(client, 52991)

    assert calls["n"] == 1


async def test_screenshots_parses_the_jpg_large_image_urls() -> None:
    entries = [
        {"jpg": {"image_url": "a.jpg", "large_image_url": "a-large.jpg"}, "webp": {}},
        {"jpg": {"image_url": "b.jpg", "large_image_url": "b-large.jpg"}, "webp": {}},
    ]

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"data": entries})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        urls = await jikan.screenshots(client, 52991)

    assert urls == ["a-large.jpg", "b-large.jpg"]


async def test_screenshots_falls_back_to_image_url_when_no_large_variant() -> None:
    entries = [{"jpg": {"image_url": "a.jpg"}, "webp": {}}]

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"data": entries})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        urls = await jikan.screenshots(client, 52991)

    assert urls == ["a.jpg"]


async def test_screenshots_returns_empty_list_when_none_exist() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"data": []})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        urls = await jikan.screenshots(client, 1)

    assert urls == []


async def test_screenshots_is_cached_for_repeated_calls() -> None:
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        return httpx.Response(200, json={"data": []})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        await jikan.screenshots(client, 52991)
        await jikan.screenshots(client, 52991)

    assert calls["n"] == 1
