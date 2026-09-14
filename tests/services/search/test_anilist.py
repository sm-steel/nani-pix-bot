import httpx
import pytest

from nani_pix_bot.services.search import anilist, cache


@pytest.fixture(autouse=True)
def _clear_cache():
    cache.clear()
    yield
    cache.clear()


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


async def test_get_by_id_parses_a_result() -> None:
    entry = {
        "id": 154587,
        "title": {
            "romaji": "Sousou no Frieren",
            "english": "Frieren: Beyond Journey's End",
            "native": "葬送のフリーレン",
        },
        "synonyms": ["Frieren"],
        "startDate": {"year": 2023},
    }

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"data": {"Media": entry}})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        result = await anilist.get_by_id(client, 154587)

    assert result == anilist.AniListResult(
        anilist_id=154587,
        title_romaji="Sousou no Frieren",
        title_english="Frieren: Beyond Journey's End",
        title_native="葬送のフリーレン",
        synonyms=["Frieren"],
        year=2023,
    )


async def test_get_by_id_returns_none_when_anilist_has_no_such_media() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"data": {"Media": None}})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        result = await anilist.get_by_id(client, 999999)

    assert result is None


async def test_search_is_cached_for_repeated_identical_queries() -> None:
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        return httpx.Response(200, json=_media_payload([]))

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        await anilist.search(client, "frieren")
        await anilist.search(client, "frieren")

    assert calls["n"] == 1


async def test_get_by_id_is_cached_for_repeated_identical_ids() -> None:
    entry = {
        "id": 154587,
        "title": {"romaji": "Sousou no Frieren", "english": None, "native": None},
        "synonyms": [],
        "startDate": None,
    }
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        return httpx.Response(200, json={"data": {"Media": entry}})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        await anilist.get_by_id(client, 154587)
        await anilist.get_by_id(client, 154587)

    assert calls["n"] == 1


async def test_search_raises_a_runtime_error_on_a_non_json_body() -> None:
    """A 200 that isn't JSON at all (a Cloudflare interstitial, say) has
    to reach the caller's _SEARCH_SERVICE_ERRORS tuple as a RuntimeError
    instead of escaping as a ValueError nobody catches (issue #75)."""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text="<html>Just a moment...</html>")

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(RuntimeError, match="non-JSON"):
            await anilist.search(client, "frieren")


async def test_search_returns_nothing_when_the_media_container_is_missing() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"data": {"Page": {}}})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        assert await anilist.search(client, "frieren") == []


async def test_search_raises_when_anilist_reports_graphql_errors() -> None:
    """A GraphQL error response carries "errors" and a null "data". That
    is an explicit "I am broken" signal, not an empty result: reporting
    "no results found" would tell the starter a flat lie about a working
    search. It has to reach _SEARCH_SERVICE_ERRORS as a RuntimeError."""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"data": None, "errors": [{"message": "boom"}]})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(RuntimeError, match="GraphQL error"):
            await anilist.search(client, "frieren")


async def test_get_by_id_raises_when_anilist_reports_graphql_errors() -> None:
    """Same for the by-id path, where the silent fallback would have told
    the starter their pick no longer exists."""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"data": None, "errors": [{"message": "boom"}]})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(RuntimeError, match="GraphQL error"):
            await anilist.get_by_id(client, 154587)


async def test_search_logs_graphql_errors_even_when_data_came_back(monkeypatch) -> None:
    """GraphQL allows partial success — data alongside errors. The usable
    half is still used, but the errors never go unrecorded."""
    entry = {
        "id": 1,
        "title": {"romaji": "Some Anime", "english": None, "native": None},
        "synonyms": [],
        "startDate": None,
    }
    logged: list[tuple] = []
    monkeypatch.setattr(anilist.logger, "error", lambda *args: logged.append(args))

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={"data": {"Page": {"media": [entry]}}, "errors": [{"message": "deprecated"}]},
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        results = await anilist.search(client, "some anime")

    assert [r.anilist_id for r in results] == [1]
    assert len(logged) == 1
    assert "deprecated" in str(logged[0])


async def test_search_returns_nothing_when_data_is_null_without_any_errors() -> None:
    """No data and no errors either is just a missing container key —
    the empty result the ticket asks for, not an outage."""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"data": None})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        assert await anilist.search(client, "frieren") == []


async def test_search_raises_a_runtime_error_on_a_literal_null_body() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text="null", headers={"Content-Type": "application/json"})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(RuntimeError, match="answered 200"):
            await anilist.search(client, "frieren")


async def test_get_by_id_returns_none_when_the_media_key_is_missing() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"data": {}})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        assert await anilist.get_by_id(client, 154587) is None


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
