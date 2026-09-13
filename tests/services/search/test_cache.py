import httpx
import pytest

from nani_pix_bot.services.search import cache


@pytest.fixture(autouse=True)
def _clear_cache():
    # The cache is keyed by client identity (see cache.py's docstring),
    # so tests are naturally isolated as long as each test builds its
    # own client — this fixture is just extra insurance against any
    # test that reuses a client across cases.
    cache.clear()
    yield
    cache.clear()


async def test_cached_calls_fetch_only_once_for_repeated_calls() -> None:
    calls = {"n": 0}

    async def fetch() -> str:
        calls["n"] += 1
        return "value"

    async with httpx.AsyncClient() as client:
        first = await cache.cached(client, "search", "frieren", fetch=fetch)
        second = await cache.cached(client, "search", "frieren", fetch=fetch)

    assert first == "value"
    assert second == "value"
    assert calls["n"] == 1


async def test_cached_calls_fetch_again_for_a_different_key() -> None:
    calls = {"n": 0}

    async def fetch() -> str:
        calls["n"] += 1
        return f"value-{calls['n']}"

    async with httpx.AsyncClient() as client:
        first = await cache.cached(client, "search", "frieren", fetch=fetch)
        second = await cache.cached(client, "search", "naruto", fetch=fetch)

    assert first == "value-1"
    assert second == "value-2"
    assert calls["n"] == 2


async def test_cached_is_scoped_to_the_client_instance() -> None:
    # Two different clients (e.g. two independent test cases, or the
    # search_client vs. tmdb_client in production) never share entries.
    calls = {"n": 0}

    async def fetch() -> str:
        calls["n"] += 1
        return f"value-{calls['n']}"

    async with httpx.AsyncClient() as client_a, httpx.AsyncClient() as client_b:
        first = await cache.cached(client_a, "search", "frieren", fetch=fetch)
        second = await cache.cached(client_b, "search", "frieren", fetch=fetch)

    assert first == "value-1"
    assert second == "value-2"
    assert calls["n"] == 2


async def test_cached_refetches_after_ttl_expires(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = {"n": 0}
    fake_time = {"now": 1000.0}
    monkeypatch.setattr(cache.time, "monotonic", lambda: fake_time["now"])

    async def fetch() -> str:
        calls["n"] += 1
        return f"value-{calls['n']}"

    async with httpx.AsyncClient() as client:
        first = await cache.cached(client, "search", "frieren", fetch=fetch, ttl=10.0)
        fake_time["now"] += 20.0  # past the 10s ttl
        second = await cache.cached(client, "search", "frieren", fetch=fetch, ttl=10.0)

    assert first == "value-1"
    assert second == "value-2"
    assert calls["n"] == 2


async def test_clear_removes_every_entry() -> None:
    async def fetch() -> str:
        return "value"

    async with httpx.AsyncClient() as client:
        await cache.cached(client, "search", "frieren", fetch=fetch)
        cache.clear()

        calls = {"n": 0}

        async def fetch_again() -> str:
            calls["n"] += 1
            return "value-again"

        result = await cache.cached(client, "search", "frieren", fetch=fetch_again)

    assert result == "value-again"
    assert calls["n"] == 1
