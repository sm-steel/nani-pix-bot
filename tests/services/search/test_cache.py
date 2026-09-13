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


async def test_cached_calls_the_wrapped_function_only_once_for_repeated_calls() -> None:
    calls = {"n": 0}

    @cache.cached()
    async def fetch(client: httpx.AsyncClient, query: str) -> str:
        calls["n"] += 1
        return "value"

    async with httpx.AsyncClient() as client:
        first = await fetch(client, "frieren")
        second = await fetch(client, "frieren")

    assert first == "value"
    assert second == "value"
    assert calls["n"] == 1


async def test_cached_calls_again_for_different_arguments() -> None:
    calls = {"n": 0}

    @cache.cached()
    async def fetch(client: httpx.AsyncClient, query: str) -> str:
        calls["n"] += 1
        return f"value-{calls['n']}"

    async with httpx.AsyncClient() as client:
        first = await fetch(client, "frieren")
        second = await fetch(client, "naruto")

    assert first == "value-1"
    assert second == "value-2"
    assert calls["n"] == 2


async def test_cached_distinguishes_keyword_arguments_too() -> None:
    """Not just positional args — search()'s `limit` is keyword-only in
    every real caller, so the key has to account for kwargs as well."""
    calls = {"n": 0}

    @cache.cached()
    async def fetch(client: httpx.AsyncClient, query: str, *, limit: int = 5) -> str:
        calls["n"] += 1
        return f"value-{calls['n']}"

    async with httpx.AsyncClient() as client:
        first = await fetch(client, "frieren", limit=5)
        second = await fetch(client, "frieren", limit=10)

    assert first == "value-1"
    assert second == "value-2"
    assert calls["n"] == 2


async def test_cached_is_scoped_to_the_client_instance() -> None:
    # Two different clients (e.g. two independent test cases, or the
    # search_client vs. tmdb_client in production) never share entries.
    calls = {"n": 0}

    @cache.cached()
    async def fetch(client: httpx.AsyncClient, query: str) -> str:
        calls["n"] += 1
        return f"value-{calls['n']}"

    async with httpx.AsyncClient() as client_a, httpx.AsyncClient() as client_b:
        first = await fetch(client_a, "frieren")
        second = await fetch(client_b, "frieren")

    assert first == "value-1"
    assert second == "value-2"
    assert calls["n"] == 2


async def test_cached_is_scoped_to_the_decorated_function() -> None:
    """Two differently-named functions decorated with @cache.cached()
    (mirroring two providers' same-named search(), or search() vs.
    get_by_id() on the same provider) never collide on the same key,
    even called with identical arguments."""
    calls = {"a": 0, "b": 0}

    @cache.cached()
    async def fetch_a(client: httpx.AsyncClient, query: str) -> str:
        calls["a"] += 1
        return "a"

    @cache.cached()
    async def fetch_b(client: httpx.AsyncClient, query: str) -> str:
        calls["b"] += 1
        return "b"

    async with httpx.AsyncClient() as client:
        first = await fetch_a(client, "frieren")
        second = await fetch_b(client, "frieren")

    assert first == "a"
    assert second == "b"
    assert calls == {"a": 1, "b": 1}


async def test_cached_refetches_after_ttl_expires(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = {"n": 0}
    fake_time = {"now": 1000.0}
    monkeypatch.setattr(cache.time, "monotonic", lambda: fake_time["now"])

    @cache.cached(ttl=10.0)
    async def fetch(client: httpx.AsyncClient, query: str) -> str:
        calls["n"] += 1
        return f"value-{calls['n']}"

    async with httpx.AsyncClient() as client:
        first = await fetch(client, "frieren")
        fake_time["now"] += 20.0  # past the 10s ttl
        second = await fetch(client, "frieren")

    assert first == "value-1"
    assert second == "value-2"
    assert calls["n"] == 2


async def test_clear_removes_every_entry() -> None:
    calls = {"n": 0}

    @cache.cached()
    async def fetch(client: httpx.AsyncClient, query: str) -> str:
        calls["n"] += 1
        return f"value-{calls['n']}"

    async with httpx.AsyncClient() as client:
        await fetch(client, "frieren")
        cache.clear()
        result = await fetch(client, "frieren")

    assert result == "value-2"
    assert calls["n"] == 2
