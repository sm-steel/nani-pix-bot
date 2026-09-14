import httpx
import pytest

from nani_pix_bot.services.search import cache, shikimori


@pytest.fixture(autouse=True)
def _clear_cache():
    cache.clear()
    yield
    cache.clear()


async def test_search_parses_a_result() -> None:
    entry = {
        "id": 52991,
        "name": "Sousou no Frieren",
        "russian": "Провожающая в последний путь Фрирен",
    }

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=[entry])

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        results = await shikimori.search(client, "frieren")

    assert results == [
        shikimori.ShikimoriResult(
            shikimori_id=52991,
            title_romaji="Sousou no Frieren",
            title_english=None,
            title_russian="Провожающая в последний путь Фрирен",
            synonyms=[],
        )
    ]


async def test_search_handles_missing_russian_title() -> None:
    entry = {"id": 1, "name": "Some Anime", "russian": None}

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=[entry])

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        results = await shikimori.search(client, "some anime")

    assert results == [
        shikimori.ShikimoriResult(
            shikimori_id=1,
            title_romaji="Some Anime",
            title_english=None,
            title_russian=None,
            synonyms=[],
        )
    ]


async def test_search_sends_a_descriptive_user_agent() -> None:
    captured: dict[str, str | None] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["user_agent"] = request.headers.get("user-agent")
        return httpx.Response(200, json=[])

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        await shikimori.search(client, "frieren")

    assert captured["user_agent"] == shikimori._REQUEST_HEADERS["User-Agent"]


async def test_search_retries_after_rate_limit_then_succeeds() -> None:
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        if calls["n"] == 1:
            return httpx.Response(429, headers={"Retry-After": "0"})
        return httpx.Response(200, json=[])

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        results = await shikimori.search(client, "frieren")

    assert results == []
    assert calls["n"] == 2


async def test_search_raises_after_exhausting_rate_limit_retries() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(429, headers={"Retry-After": "0"})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(RuntimeError, match="rate limit"):
            await shikimori.search(client, "frieren")


async def test_get_by_id_parses_the_richer_detail_fields() -> None:
    entry = {
        "id": 52991,
        "name": "Sousou no Frieren",
        "russian": "Провожающая в последний путь Фрирен",
        "english": ["Frieren: Beyond Journey's End"],
        "japanese": ["葬送のフリーレン"],
        "synonyms": ["Фрирен, провожающая в последний путь", "Frieren at the Funeral"],
    }

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=entry)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        result = await shikimori.get_by_id(client, 52991)

    assert result == shikimori.ShikimoriResult(
        shikimori_id=52991,
        title_romaji="Sousou no Frieren",
        title_english="Frieren: Beyond Journey's End",
        title_russian="Провожающая в последний путь Фрирен",
        synonyms=["Фрирен, провожающая в последний путь", "Frieren at the Funeral"],
    )


async def test_get_by_id_handles_missing_english_and_synonyms() -> None:
    entry = {"id": 1, "name": "Some Anime", "russian": None}

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=entry)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        result = await shikimori.get_by_id(client, 1)

    assert result == shikimori.ShikimoriResult(
        shikimori_id=1,
        title_romaji="Some Anime",
        title_english=None,
        title_russian=None,
        synonyms=[],
    )


async def test_get_by_id_returns_none_when_shikimori_has_no_such_anime() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(404, json={"error": "not found"})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        result = await shikimori.get_by_id(client, 999999)

    assert result is None


async def test_search_is_cached_for_repeated_identical_queries() -> None:
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        return httpx.Response(200, json=[])

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        await shikimori.search(client, "frieren")
        await shikimori.search(client, "frieren")

    assert calls["n"] == 1


async def test_get_by_id_is_cached_for_repeated_identical_ids() -> None:
    entry = {"id": 52991, "name": "Sousou no Frieren", "russian": None}
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        return httpx.Response(200, json=entry)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        await shikimori.get_by_id(client, 52991)
        await shikimori.get_by_id(client, 52991)

    assert calls["n"] == 1


async def test_screenshots_parses_and_prefixes_relative_urls() -> None:
    entries = [
        {"original": "/system/screenshots/original/a.jpg?1", "preview": "/x/a.jpg?1"},
        {"original": "/system/screenshots/original/b.jpg?2", "preview": "/x/b.jpg?2"},
    ]

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=entries)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        urls = await shikimori.screenshots(client, 52991)

    assert urls == [
        "https://shikimori.io/system/screenshots/original/a.jpg?1",
        "https://shikimori.io/system/screenshots/original/b.jpg?2",
    ]


async def test_screenshots_caps_at_the_fetch_limit() -> None:
    entries = [
        {"original": f"/system/screenshots/original/{i}.jpg", "preview": f"/x/{i}.jpg"}
        for i in range(30)
    ]

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=entries)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        urls = await shikimori.screenshots(client, 52991)

    assert len(urls) == shikimori.SCREENSHOT_FETCH_LIMIT


async def test_screenshots_returns_empty_list_when_none_exist() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=[])

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        urls = await shikimori.screenshots(client, 1)

    assert urls == []


async def test_search_raises_a_runtime_error_on_a_non_json_body() -> None:
    """Shikimori is reached over a proxy, which can answer 200 with its
    own HTML error page — that must surface as a RuntimeError the
    handlers already catch (issue #75)."""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text="<html>Bad gateway</html>")

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(RuntimeError, match="non-JSON"):
            await shikimori.search(client, "frieren")


async def test_search_raises_a_runtime_error_on_a_literal_null_body() -> None:
    """Shikimori's list endpoints answer with a bare array; a 200 that
    decodes to `null` instead is not an empty list, and iterating it
    raises a TypeError no handler catches."""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text="null", headers={"Content-Type": "application/json"})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(RuntimeError, match="answered 200"):
            await shikimori.search(client, "frieren")


async def test_screenshots_raises_when_an_object_arrives_instead_of_an_array() -> None:
    """The screenshots endpoint is declared as a list endpoint, so an
    error object where the array should be is an outage, not zero
    screenshots."""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"message": "something went wrong"})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(RuntimeError, match="expected list"):
            await shikimori.screenshots(client, 52991)


async def test_screenshots_skips_entries_with_no_original_path() -> None:
    entries = [
        {"original": "/system/screenshots/original/a.jpg?1", "preview": "/x/a.jpg?1"},
        {"preview": "/x/b.jpg?2"},
        {"original": None, "preview": "/x/c.jpg?3"},
    ]

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=entries)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        urls = await shikimori.screenshots(client, 52991)

    assert urls == ["https://shikimori.io/system/screenshots/original/a.jpg?1"]


async def test_screenshots_is_cached_for_repeated_calls() -> None:
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        return httpx.Response(200, json=[])

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        await shikimori.screenshots(client, 52991)
        await shikimori.screenshots(client, 52991)

    assert calls["n"] == 1
