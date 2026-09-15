from dataclasses import dataclass

import httpx
import pytest

from nani_pix_bot.services.search import rest


@dataclass(frozen=True)
class _Parsed:
    identifier: int
    title: str


def _parse(raw: dict) -> _Parsed:
    return _Parsed(identifier=raw["id"], title=raw["title"])


_API = rest.RestApi(name="Example", headers={"User-Agent": "nani-pix-bot"})


async def test_get_json_returns_the_decoded_body() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"id": 7, "title": "Frieren"})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        body = await rest.get_json(_API, client, "https://example.test/anime/7", {})

    assert body == {"id": 7, "title": "Frieren"}


async def test_get_json_decodes_a_top_level_list() -> None:
    """Shikimori's list endpoint answers with a bare JSON array, not an
    object — the shared helper must not assume a dict."""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=[{"id": 1}, {"id": 2}])

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        body = await rest.get_json(_API, client, "https://example.test/animes", {}, expect=list)

    assert body == [{"id": 1}, {"id": 2}]


async def test_get_json_sends_the_apis_headers_and_params() -> None:
    seen: dict[str, str] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["user_agent"] = request.headers.get("user-agent", "")
        seen["query"] = request.url.query.decode()
        return httpx.Response(200, json={})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        await rest.get_json(_API, client, "https://example.test/animes", {"search": "frieren"})

    assert seen["user_agent"] == "nani-pix-bot"
    assert seen["query"] == "search=frieren"


async def test_get_json_works_for_an_api_with_no_headers_of_its_own() -> None:
    """TMDB sets its Bearer token on the client itself, so it declares
    no per-request headers — that must not wipe the client's defaults."""
    api = rest.RestApi(name="Example")
    seen: dict[str, str] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["auth"] = request.headers.get("authorization", "")
        return httpx.Response(200, json={})

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(handler), headers={"Authorization": "Bearer token"}
    ) as client:
        await rest.get_json(api, client, "https://example.test/tv/1", {})

    assert seen["auth"] == "Bearer token"


async def test_get_json_raises_on_a_server_error() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(504)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(httpx.HTTPStatusError):
            await rest.get_json(_API, client, "https://example.test/anime/7", {})


async def test_get_json_converts_a_non_json_body_into_a_runtime_error() -> None:
    """A throttle/proxy error page served as HTML with a 200 must not
    escape as a bare ValueError — nothing on the handler side catches
    that, so the starter would be left with no reply at all (issue #75)."""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text="<html><body>Too many requests</body></html>")

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(RuntimeError, match="non-JSON"):
            await rest.get_json(_API, client, "https://example.test/anime/7", {})


async def test_get_json_rejects_a_literal_null_body() -> None:
    """`json.loads("null")` succeeds and hands back None, so the decode
    guard alone doesn't catch it — None then reaches every caller's
    `.get`/`[...]`/iteration as an AttributeError or TypeError that no
    handler catches, which is the same dead-keyboard outcome by a
    different door."""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text="null", headers={"Content-Type": "application/json"})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(RuntimeError, match="answered 200"):
            await rest.get_json(_API, client, "https://example.test/anime/7", {})


async def test_get_json_rejects_a_scalar_body() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json="rate limited")

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(RuntimeError, match="answered 200"):
            await rest.get_json(_API, client, "https://example.test/anime/7", {})


async def test_get_json_rejects_an_array_where_an_object_is_expected() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=[])

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(RuntimeError, match="expected dict"):
            await rest.get_json(_API, client, "https://example.test/anime/7", {})


async def test_get_json_rejects_an_object_where_an_array_is_expected() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"error": "nope"})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(RuntimeError, match="expected list"):
            await rest.get_json(_API, client, "https://example.test/animes", {}, expect=list)


async def test_fetch_by_id_does_not_swallow_a_null_body() -> None:
    """A null body is not "this id is gone" either — the 404 branch must
    not absorb it into a None that reads as a removed entry."""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text="null", headers={"Content-Type": "application/json"})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(RuntimeError, match="answered 200"):
            await rest.fetch_by_id(_API, client, "https://example.test/anime/7", 7, _parse)


async def test_fetch_by_id_does_not_swallow_a_non_json_body() -> None:
    """The 404 branch turns a missing entity into None; a malformed body
    is not a missing entity and must still surface as an error."""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text="not json")

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(RuntimeError, match="non-JSON"):
            await rest.fetch_by_id(_API, client, "https://example.test/anime/7", 7, _parse)


async def test_fetch_by_id_parses_a_found_entity() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"id": 7, "title": "Frieren"})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        result = await rest.fetch_by_id(_API, client, "https://example.test/anime/7", 7, _parse)

    assert result == _Parsed(identifier=7, title="Frieren")


async def test_fetch_by_id_returns_none_when_the_id_is_gone() -> None:
    """A 404 means the starter tapped a result that no longer exists —
    a normal outcome the picker reports, not an error."""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(404)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        result = await rest.fetch_by_id(_API, client, "https://example.test/anime/7", 7, _parse)

    assert result is None


async def test_fetch_by_id_reraises_anything_that_is_not_a_404() -> None:
    """A provider being down is not "this id is gone" — it has to reach
    the caller's _SEARCH_SERVICE_ERRORS handling instead of silently
    looking like an empty result."""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(504)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(httpx.HTTPStatusError):
            await rest.fetch_by_id(_API, client, "https://example.test/anime/7", 7, _parse)
