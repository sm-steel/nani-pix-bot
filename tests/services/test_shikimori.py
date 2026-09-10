import httpx
import pytest

from nani_pix_bot.services import shikimori


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
