import httpx
import pytest

from nani_pix_bot.services.search import shikimori, tenrai
from nani_pix_bot.services.search.tags import AnimeTag, from_json, to_json


def _client(status: int, body: object) -> httpx.AsyncClient:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(status, json=body)

    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


async def test_tenrai_get_tags_reads_genres_and_themes() -> None:
    body = {
        "data": {
            "mal_id": 4224,
            "genres": [{"mal_id": 22, "name": "Romance"}],
            "themes": [{"mal_id": 23, "name": "School"}],
        }
    }
    async with _client(200, body) as client:
        tags = await tenrai.get_tags(client, 4224)

    assert tags == [AnimeTag("genre", "Romance", 22), AnimeTag("theme", "School", 23)]


async def test_tenrai_get_tags_skips_malformed_entries() -> None:
    body = {
        "data": {"genres": [{"mal_id": "x", "name": "Bad"}, "junk", {"mal_id": 1, "name": "Ok"}]}
    }
    async with _client(200, body) as client:
        tags = await tenrai.get_tags(client, 1)

    assert tags == [AnimeTag("genre", "Ok", 1)]


async def test_tenrai_get_tags_without_data_is_none() -> None:
    async with _client(200, {"data": None}) as client:
        assert await tenrai.get_tags(client, 1) is None


async def test_tenrai_get_tags_404_is_none() -> None:
    async with _client(404, {"error": "not found"}) as client:
        assert await tenrai.get_tags(client, 1) is None


async def test_tenrai_get_tags_500_raises() -> None:
    async with _client(500, {}) as client:
        with pytest.raises(httpx.HTTPStatusError):
            await tenrai.get_tags(client, 1)


async def test_shikimori_get_tags_matches_by_name_without_mal_id() -> None:
    body = {
        "data": {
            "animes": [
                {
                    "id": "4224",
                    "genres": [
                        {"id": "22", "name": "Romance", "kind": "genre"},
                        {"id": "107", "name": "Love Polygon", "kind": "theme"},
                    ],
                }
            ]
        }
    }
    async with _client(200, body) as client:
        tags = await shikimori.get_tags(client, 4224)

    assert tags == [AnimeTag("genre", "Romance", None), AnimeTag("theme", "Love Polygon", None)]


async def test_shikimori_get_tags_skips_malformed_genres() -> None:
    body = {"data": {"animes": [{"id": "1", "genres": [{"name": "NoKind"}, "junk", None]}]}}
    async with _client(200, body) as client:
        assert await shikimori.get_tags(client, 1) == []


async def test_shikimori_get_tags_empty_animes_is_none() -> None:
    async with _client(200, {"data": {"animes": []}}) as client:
        assert await shikimori.get_tags(client, 1) is None


def test_json_round_trip() -> None:
    tags = [AnimeTag("genre", "Romance", 22), AnimeTag("theme", "Love Polygon", None)]

    assert from_json(to_json(tags)) == tags


def test_from_json_skips_malformed_entries() -> None:
    raw = [
        {"kind": "genre", "name": "Ok", "mal_id": 1},
        {"kind": "genre"},
        {"name": "NoKind"},
        "junk",
        {"kind": "theme", "name": "BadId", "mal_id": "x"},
    ]

    assert from_json(raw) == [AnimeTag("genre", "Ok", 1), AnimeTag("theme", "BadId", None)]


def test_from_json_none_is_empty() -> None:
    assert from_json(None) == []
