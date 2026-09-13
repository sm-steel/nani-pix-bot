import httpx
import pytest

from nani_pix_bot.services.search import cache, tmdb


@pytest.fixture(autouse=True)
def _clear_cache():
    cache.clear()
    yield
    cache.clear()


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


async def test_search_is_cached_for_repeated_identical_queries() -> None:
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        return httpx.Response(200, json={"results": []})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        await tmdb.search(client, "frieren")
        await tmdb.search(client, "frieren")

    assert calls["n"] == 1


async def test_get_by_id_is_cached_for_repeated_identical_ids() -> None:
    entry = {"id": 209867, "name": "Frieren", "original_name": None}
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        return httpx.Response(200, json=entry)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        await tmdb.get_by_id(client, 209867)
        await tmdb.get_by_id(client, 209867)

    assert calls["n"] == 1


def _season_episode_handler(*, episode_count: int, still_paths: dict[int, str | None]):
    """Routes /tv/{id} (show detail, one season) and
    /tv/{id}/season/{s}/episode/{e} (per-episode still_path) requests to
    the right canned response, by inspecting the request path."""

    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path.count("/episode/") == 1:
            episode_number = int(path.rsplit("/episode/", 1)[1])
            still_path = still_paths.get(episode_number)
            return httpx.Response(200, json={"still_path": still_path})
        return httpx.Response(
            200,
            json={
                "id": 209867,
                "name": "Frieren",
                "original_name": None,
                "seasons": [{"season_number": 1, "episode_count": episode_count}],
            },
        )

    return handler


async def test_screenshots_fetches_stills_for_the_first_seasons_episodes() -> None:
    handler = _season_episode_handler(
        episode_count=3, still_paths={1: "/still1.jpg", 2: "/still2.jpg", 3: "/still3.jpg"}
    )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        urls = await tmdb.screenshots(client, 209867)

    assert urls == [
        f"{tmdb.TMDB_IMAGE_BASE_URL}/still1.jpg",
        f"{tmdb.TMDB_IMAGE_BASE_URL}/still2.jpg",
        f"{tmdb.TMDB_IMAGE_BASE_URL}/still3.jpg",
    ]


async def test_screenshots_skips_episodes_with_no_still() -> None:
    handler = _season_episode_handler(episode_count=2, still_paths={1: "/still1.jpg", 2: None})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        urls = await tmdb.screenshots(client, 209867)

    assert urls == [f"{tmdb.TMDB_IMAGE_BASE_URL}/still1.jpg"]


async def test_screenshots_caps_at_the_fetch_limit() -> None:
    still_paths: dict[int, str | None] = {n: f"/still{n}.jpg" for n in range(1, 31)}
    handler = _season_episode_handler(episode_count=30, still_paths=still_paths)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        urls = await tmdb.screenshots(client, 209867)

    assert len(urls) == tmdb.SCREENSHOT_FETCH_LIMIT


async def test_screenshots_returns_empty_list_when_no_seasons_exist() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200, json={"id": 1, "name": "Some Anime", "original_name": None, "seasons": []}
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        urls = await tmdb.screenshots(client, 1)

    assert urls == []


async def test_screenshots_is_cached_for_repeated_calls() -> None:
    calls = {"n": 0}
    handler = _season_episode_handler(episode_count=1, still_paths={1: "/still1.jpg"})

    def counting_handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        return handler(request)

    async with httpx.AsyncClient(transport=httpx.MockTransport(counting_handler)) as client:
        await tmdb.screenshots(client, 209867)
        await tmdb.screenshots(client, 209867)

    assert calls["n"] == 2  # 1 show-detail + 1 episode call, not doubled
