import asyncio
from typing import Any

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


async def test_search_raises_a_runtime_error_on_a_non_json_body() -> None:
    """TMDB is reached through a proxy, whose own error pages come back
    as HTML with whatever status it likes — a 200 among them must become
    a RuntimeError the handlers already catch (issue #75)."""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text="<html>Proxy error</html>")

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(RuntimeError, match="non-JSON"):
            await tmdb.search(client, "frieren")


async def test_search_raises_a_runtime_error_on_a_literal_null_body() -> None:
    """A 200 carrying `null` decodes fine, so the decode guard misses it;
    None then reaches `.get` as an AttributeError no handler catches."""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text="null", headers={"Content-Type": "application/json"})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(RuntimeError, match="answered 200"):
            await tmdb.search(client, "frieren")


async def test_search_returns_nothing_when_the_results_container_is_null() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"results": None})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        assert await tmdb.search(client, "frieren") == []


async def test_screenshots_returns_empty_list_when_the_seasons_key_is_null() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"id": 1, "name": "Some Anime", "seasons": None})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        assert await tmdb.screenshots(client, 1) == []


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


def _show_handler(seasons: list[Any], still_paths: dict[tuple[int, int], str | None]):
    """Routes /tv/{id} and /tv/{id}/season/{s}/episode/{e} like
    `_season_episode_handler` above, but takes the show's raw `seasons`
    array verbatim (so a test can hand TMDB a null field) and keys its
    stills by `(season_number, episode_number)` rather than assuming one
    season."""

    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path.count("/episode/") == 1:
            season_number = int(path.split("/season/", 1)[1].split("/episode/", 1)[0])
            episode_number = int(path.rsplit("/episode/", 1)[1])
            still = still_paths.get((season_number, episode_number))
            return httpx.Response(200, json={"still_path": still})
        return httpx.Response(
            200, json={"id": 1, "name": "Some Anime", "original_name": None, "seasons": seasons}
        )

    return handler


async def test_screenshots_falls_through_to_later_seasons_for_stills() -> None:
    """A show whose season 1 carries no stills still has a gallery if a
    later season does — `seasons[0]` alone used to return nothing."""
    handler = _show_handler(
        seasons=[
            {"season_number": 1, "episode_count": 2},
            {"season_number": 2, "episode_count": 2},
        ],
        still_paths={(1, 1): None, (1, 2): None, (2, 1): "/s2e1.jpg", (2, 2): "/s2e2.jpg"},
    )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        urls = await tmdb.screenshots(client, 1)

    assert urls == [
        f"{tmdb.TMDB_IMAGE_BASE_URL}/s2e1.jpg",
        f"{tmdb.TMDB_IMAGE_BASE_URL}/s2e2.jpg",
    ]


async def test_screenshots_visits_seasons_in_ascending_order() -> None:
    """The gallery resolves `screenshot_pick:tmdb:<index>` against this
    list, so the order can't depend on how TMDB happened to sort its
    own `seasons` array."""
    handler = _show_handler(
        seasons=[
            {"season_number": 2, "episode_count": 1},
            {"season_number": 1, "episode_count": 1},
        ],
        still_paths={(1, 1): "/s1e1.jpg", (2, 1): "/s2e1.jpg"},
    )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        urls = await tmdb.screenshots(client, 1)

    assert urls == [
        f"{tmdb.TMDB_IMAGE_BASE_URL}/s1e1.jpg",
        f"{tmdb.TMDB_IMAGE_BASE_URL}/s2e1.jpg",
    ]


async def test_screenshots_skips_a_season_whose_number_is_null() -> None:
    """`s.get("season_number", 0)` returns None, not 0, when the key is
    present and null — and `None >= 1` raises TypeError (issue #76)."""
    handler = _show_handler(seasons=[{"season_number": None, "episode_count": 3}], still_paths={})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        assert await tmdb.screenshots(client, 1) == []


async def test_screenshots_treats_a_null_episode_count_as_no_episodes() -> None:
    """Same shape as the null season_number above, one line down:
    `min(None, 20)` raises TypeError (issue #76). The season with a real
    count still contributes."""
    handler = _show_handler(
        seasons=[
            {"season_number": 1, "episode_count": None},
            {"season_number": 2, "episode_count": 1},
        ],
        still_paths={(2, 1): "/s2e1.jpg"},
    )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        urls = await tmdb.screenshots(client, 1)

    assert urls == [f"{tmdb.TMDB_IMAGE_BASE_URL}/s2e1.jpg"]


async def test_screenshots_returns_empty_list_when_every_season_is_empty() -> None:
    handler = _show_handler(seasons=[{"season_number": 1, "episode_count": None}], still_paths={})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        assert await tmdb.screenshots(client, 1) == []


async def test_screenshots_caps_total_episode_requests_across_all_seasons() -> None:
    """The fetch limit bounds the whole call, not each season — walking
    every season of a long-running show would be unbounded."""
    seasons = [{"season_number": n, "episode_count": 15} for n in (1, 2, 3)]
    still_paths: dict[tuple[int, int], str | None] = {
        (season, episode): f"/s{season}e{episode}.jpg"
        for season in (1, 2, 3)
        for episode in range(1, 16)
    }
    episode_calls = []
    handler = _show_handler(seasons=seasons, still_paths=still_paths)

    def counting_handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.count("/episode/") == 1:
            episode_calls.append(request.url.path)
        return handler(request)

    async with httpx.AsyncClient(transport=httpx.MockTransport(counting_handler)) as client:
        urls = await tmdb.screenshots(client, 1)

    assert len(episode_calls) == tmdb.SCREENSHOT_FETCH_LIMIT
    assert len(urls) == tmdb.SCREENSHOT_FETCH_LIMIT
    assert urls[0] == f"{tmdb.TMDB_IMAGE_BASE_URL}/s1e1.jpg"
    assert urls[-1] == f"{tmdb.TMDB_IMAGE_BASE_URL}/s2e5.jpg"


async def test_screenshots_keeps_episode_order_when_responses_finish_out_of_order() -> None:
    """Completion order must never leak into the returned list: the
    gallery resolves `screenshot_pick:tmdb:<index>` against it. This
    handler answers the *last* episode first."""
    episode_count = 6
    still_paths: dict[tuple[int, int], str | None] = {
        (1, n): f"/s1e{n}.jpg" for n in range(1, episode_count + 1)
    }
    handler = _show_handler(
        seasons=[{"season_number": 1, "episode_count": episode_count}], still_paths=still_paths
    )

    async def slow_handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path.count("/episode/") == 1:
            episode_number = int(path.rsplit("/episode/", 1)[1])
            await asyncio.sleep(0.01 * (episode_count - episode_number))
        return handler(request)

    async with httpx.AsyncClient(transport=httpx.MockTransport(slow_handler)) as client:
        urls = await tmdb.screenshots(client, 1)

    assert urls == [f"{tmdb.TMDB_IMAGE_BASE_URL}/s1e{n}.jpg" for n in range(1, episode_count + 1)]


async def test_screenshots_fetches_episodes_concurrently_but_bounded() -> None:
    """20 sequential round trips through the amsterdam proxy is minutes
    of the starter staring at nothing; firing all 20 at once at a single
    tinyproxy is the other extreme."""
    in_flight = 0
    peak = 0
    handler = _show_handler(
        seasons=[{"season_number": 1, "episode_count": 20}],
        still_paths={(1, n): f"/s1e{n}.jpg" for n in range(1, 21)},
    )

    async def tracking_handler(request: httpx.Request) -> httpx.Response:
        nonlocal in_flight, peak
        if request.url.path.count("/episode/") != 1:
            return handler(request)
        in_flight += 1
        peak = max(peak, in_flight)
        await asyncio.sleep(0.01)
        in_flight -= 1
        return handler(request)

    async with httpx.AsyncClient(transport=httpx.MockTransport(tracking_handler)) as client:
        urls = await tmdb.screenshots(client, 1)

    assert len(urls) == 20
    assert peak > 1
    assert peak <= tmdb.SCREENSHOT_FETCH_CONCURRENCY


async def test_screenshots_stops_issuing_requests_once_an_episode_fails() -> None:
    """`asyncio.gather` propagates the first failure but leaves its
    siblings running: every remaining episode request still went out,
    and landed after the starter already had their error message. The
    starter-facing half was always fine (screenshots.py catches it and
    renders a live source menu) — what's worth not sending is the volume,
    through the one proxy Telegram's polling shares, on exactly the path
    where the remote is already misbehaving."""
    issued: list[int] = []
    still_paths: dict[tuple[int, int], str | None] = {(1, n): f"/s1e{n}.jpg" for n in range(1, 13)}
    handler = _show_handler(
        seasons=[{"season_number": 1, "episode_count": 12}], still_paths=still_paths
    )

    async def failing_handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path.count("/episode/") != 1:
            return handler(request)
        episode_number = int(path.rsplit("/episode/", 1)[1])
        issued.append(episode_number)
        if episode_number == 2:
            return httpx.Response(500)
        await asyncio.sleep(0.05)
        return handler(request)

    async with httpx.AsyncClient(transport=httpx.MockTransport(failing_handler)) as client:
        with pytest.raises(httpx.HTTPStatusError):
            await tmdb.screenshots(client, 1)
        at_raise_time = sorted(issued)
        await asyncio.sleep(0.3)  # let anything still detached run to completion

    assert sorted(issued) == at_raise_time, "requests kept going out after the caller had failed"
    # One more than the concurrency limit, not one fewer: the failing
    # request releases its own semaphore slot while unwinding, so exactly
    # one queued episode gets in before the group aborts the rest. Twelve
    # is what `gather` produced.
    assert len(issued) <= tmdb.SCREENSHOT_FETCH_CONCURRENCY + 1
    assert 12 not in issued


async def test_search_skips_an_entry_with_no_id() -> None:
    """A third party controls these keys: one entry missing the one field
    the parser indexes must not cost the starter the other results
    (issue #83)."""
    entries = [{"name": "No id here"}, {"id": 209867, "name": "Frieren"}]

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"results": entries})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        results = await tmdb.search(client, "frieren")

    assert [result.tmdb_id for result in results] == [209867]


async def test_search_skips_scalar_entries() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"results": [1, 2]})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        results = await tmdb.search(client, "frieren")

    assert results == []


async def test_get_by_id_returns_none_when_the_entry_has_no_id() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"name": "Frieren"})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        result = await tmdb.get_by_id(client, 209867)

    assert result is None


async def test_screenshots_skips_a_scalar_season_entry() -> None:
    """season.get("season_number") on an int is an AttributeError — the
    season list is third-party-controlled the same way a result list is."""
    handler = _show_handler([1, {"season_number": 1, "episode_count": 1}], {(1, 1): "/still1.jpg"})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        urls = await tmdb.screenshots(client, 209867)

    assert urls == [f"{tmdb.TMDB_IMAGE_BASE_URL}/still1.jpg"]


async def test_screenshots_skips_a_season_with_no_season_number() -> None:
    handler = _show_handler(
        [{"episode_count": 3}, {"season_number": 1, "episode_count": 1}], {(1, 1): "/still1.jpg"}
    )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        urls = await tmdb.screenshots(client, 209867)

    assert urls == [f"{tmdb.TMDB_IMAGE_BASE_URL}/still1.jpg"]


async def test_search_skips_an_entry_whose_id_is_null() -> None:
    """`raw["id"]` succeeds for a JSON null, so the missing-key guard alone
    would stage a result with tmdb_id=None and build a `tmdb_pick:None`
    button that cannot work (issue #83)."""
    entries = [{"id": None, "name": "Null id"}, {"id": 209867, "name": "Frieren"}]

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"results": entries})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        results = await tmdb.search(client, "frieren")

    assert [result.tmdb_id for result in results] == [209867]


async def test_get_by_id_returns_none_when_the_id_is_null() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"id": None, "name": "Frieren"})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        result = await tmdb.get_by_id(client, 209867)

    assert result is None


async def test_screenshots_skips_a_season_whose_episode_count_is_a_string() -> None:
    """`episode_count` is consumed at `min(episode_count, remaining)` after
    the guard has already returned, so a non-integer there escaped as a raw
    TypeError no handler catches. Validating it inside `_parse_season` routes
    it through the same skip every other malformation gets (issue #83)."""
    handler = _show_handler(
        [
            {"season_number": 1, "episode_count": "3"},
            {"season_number": 2, "episode_count": 1},
        ],
        {(2, 1): "/still2.jpg"},
    )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        urls = await tmdb.screenshots(client, 209867)

    assert urls == [f"{tmdb.TMDB_IMAGE_BASE_URL}/still2.jpg"]


async def test_screenshots_skips_a_season_whose_episode_count_is_a_float() -> None:
    handler = _show_handler(
        [{"season_number": 1, "episode_count": 2.5}, {"season_number": 2, "episode_count": 1}],
        {(2, 1): "/still2.jpg"},
    )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        urls = await tmdb.screenshots(client, 209867)

    assert urls == [f"{tmdb.TMDB_IMAGE_BASE_URL}/still2.jpg"]


async def test_screenshots_skips_a_season_whose_episode_count_is_a_list() -> None:
    handler = _show_handler(
        [{"season_number": 1, "episode_count": [1]}, {"season_number": 2, "episode_count": 1}],
        {(2, 1): "/still2.jpg"},
    )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        urls = await tmdb.screenshots(client, 209867)

    assert urls == [f"{tmdb.TMDB_IMAGE_BASE_URL}/still2.jpg"]


async def test_screenshots_skips_a_season_whose_number_is_a_float() -> None:
    """A float season_number compares against 1 without raising, so it
    escaped the guard the same way episode_count did and was interpolated
    straight into a request URL (issue #83)."""
    handler = _show_handler(
        [{"season_number": 2.5, "episode_count": 1}, {"season_number": 2, "episode_count": 1}],
        {(2, 1): "/still2.jpg"},
    )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        urls = await tmdb.screenshots(client, 209867)

    assert urls == [f"{tmdb.TMDB_IMAGE_BASE_URL}/still2.jpg"]


async def test_screenshots_skips_a_season_whose_number_is_a_bool() -> None:
    """bool is an int subclass, so True compares as 1 and would have asked
    TMDB for /season/True/episode/1."""
    handler = _show_handler(
        [{"season_number": True, "episode_count": 1}, {"season_number": 2, "episode_count": 1}],
        {(2, 1): "/still2.jpg"},
    )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        urls = await tmdb.screenshots(client, 209867)

    assert urls == [f"{tmdb.TMDB_IMAGE_BASE_URL}/still2.jpg"]


async def test_screenshots_skips_a_season_whose_number_is_a_string() -> None:
    handler = _show_handler(
        [{"season_number": "1", "episode_count": 1}, {"season_number": 2, "episode_count": 1}],
        {(2, 1): "/still2.jpg"},
    )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        urls = await tmdb.screenshots(client, 209867)

    assert urls == [f"{tmdb.TMDB_IMAGE_BASE_URL}/still2.jpg"]


_GOOD_ENTRY = {"id": 209867, "name": "Frieren", "original_name": "葬送のフリーレン"}


@pytest.mark.parametrize(
    "field",
    [
        pytest.param({"name": 5}, id="name-is-a-number"),
        pytest.param({"name": ["Frieren"]}, id="name-is-an-array"),
        pytest.param({"original_name": {"jp": "x"}}, id="original-name-is-an-object"),
        pytest.param({"original_name": True}, id="original-name-is-a-bool"),
    ],
)
async def test_search_skips_an_entry_with_a_malformed_title(field: dict) -> None:
    """TMDB has no synonyms to corrupt, but its two title fields are the
    same defect as everyone else's (issue #86)."""
    bad = {**_GOOD_ENTRY, "id": 1, **field}

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"results": [bad, _GOOD_ENTRY]})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        results = await tmdb.search(client, "frieren")

    assert [result.tmdb_id for result in results] == [209867]


async def test_search_warns_naming_the_parser_when_a_title_is_malformed(
    records: list[tuple[str, str]],
) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"results": [{**_GOOD_ENTRY, "name": 5}]})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        await tmdb.search(client, "frieren")

    warnings = [message for level, message in records if level == "WARNING"]
    assert len(warnings) == 1
    assert "TMDB" in warnings[0]
    assert "_parse_result" in warnings[0]
    assert "name" in warnings[0]


@pytest.mark.parametrize(
    "field",
    [
        pytest.param({"name": 5}, id="name-is-a-number"),
        pytest.param({"original_name": ["x"]}, id="original-name-is-an-array"),
    ],
)
async def test_get_by_id_returns_none_for_a_malformed_title(field: dict) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={**_GOOD_ENTRY, **field})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        assert await tmdb.get_by_id(client, 209867) is None


# issue #89: a well-typed entry whose titles are all null/absent/empty
# stages an unwinnable game (an empty match_candidates() list) that looks
# exactly like a working one. TMDB has no synonyms field at all (see the
# module docstring), so unlike the other three providers there's no
# "no title but has synonyms survives" case to test here — for TMDB the
# general rule (parsing.has_answer_key) always reduces to "skip when both
# titles are empty." Explicit per-case entries rather than merging over
# _GOOD_ENTRY: the "absent entirely" case needs the keys gone, not
# present-and-null, which a dict merge can't express.
_NO_TITLE_ENTRIES = [
    pytest.param({"id": 1, "name": None, "original_name": None}, id="all-titles-null"),
    pytest.param({"id": 1, "name": "", "original_name": ""}, id="all-titles-empty-string"),
    pytest.param({"id": 1}, id="titles-absent-entirely"),
]


@pytest.mark.parametrize("bad", _NO_TITLE_ENTRIES)
async def test_search_skips_an_entry_with_no_title(bad: dict) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"results": [bad, _GOOD_ENTRY]})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        results = await tmdb.search(client, "frieren")

    assert [result.tmdb_id for result in results] == [209867]


@pytest.mark.parametrize("entry", _NO_TITLE_ENTRIES)
async def test_get_by_id_returns_none_when_the_entry_has_no_title(entry: dict) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=entry)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        assert await tmdb.get_by_id(client, 209867) is None


async def test_search_keeps_a_legitimately_sparse_entry_with_one_title_variant() -> None:
    """Regression guard: one populated title variant, the rest null, is
    winnable and must not be caught by the no-title check."""
    entry = {"id": 1, "name": "Some Anime", "original_name": None}

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"results": [entry]})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        results = await tmdb.search(client, "frieren")

    assert [result.tmdb_id for result in results] == [1]


@pytest.mark.parametrize(
    "still_path",
    [
        pytest.param({"a": 1}, id="still-path-is-an-object"),
        pytest.param(5, id="still-path-is-a-number"),
        pytest.param(["/still1.jpg"], id="still-path-is-an-array"),
        pytest.param(True, id="still-path-is-a-bool"),
    ],
)
async def test_screenshots_drops_an_episode_whose_still_path_is_not_a_string(
    still_path: object,
) -> None:
    """The still read sits inside the TaskGroup rather than inside a
    `_parse_*` function, so it had no guard at all — and a raise there
    would surface as a TypeError `_SEARCH_SERVICE_ERRORS` doesn't match.
    It goes through `parse_entry` for exactly that reason: one unusable
    episode costs its own still, not the gallery (issue #86)."""

    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path.count("/episode/") == 1:
            episode_number = int(path.rsplit("/episode/", 1)[1])
            return httpx.Response(
                200, json={"still_path": still_path if episode_number == 1 else "/still2.jpg"}
            )
        return httpx.Response(
            200,
            json={
                "id": 209867,
                "name": "Frieren",
                "original_name": None,
                "seasons": [{"season_number": 1, "episode_count": 2}],
            },
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        urls = await tmdb.screenshots(client, 209867)

    assert urls == [f"{tmdb.TMDB_IMAGE_BASE_URL}/still2.jpg"]
