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


async def test_search_requests_only_sfw_results() -> None:
    """Results go into a shared group topic, so the one parameter Jikan
    offers for this is cheap insurance (TMDB's `include_adult` already
    defaults false)."""
    captured: dict[str, str | None] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["sfw"] = request.url.params.get("sfw")
        return httpx.Response(200, json={"data": []})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        await jikan.search(client, "frieren")

    assert captured["sfw"] == "true"


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


async def test_search_raises_a_runtime_error_on_a_non_json_body() -> None:
    """Jikan in front of a proxy can answer 200 with an HTML error page;
    that has to become a RuntimeError the handlers already catch, not a
    ValueError that strands the starter (issue #75)."""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text="<html>503 Service Unavailable</html>")

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(RuntimeError, match="non-JSON"):
            await jikan.search(client, "frieren")


async def test_search_raises_a_runtime_error_on_a_literal_null_body() -> None:
    """A 200 carrying `null` decodes fine, so the decode guard misses it;
    None then reaches `.get` as an AttributeError no handler catches."""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text="null", headers={"Content-Type": "application/json"})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(RuntimeError, match="answered 200"):
            await jikan.search(client, "frieren")


async def test_search_returns_nothing_when_the_data_container_is_null() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"data": None})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        assert await jikan.search(client, "frieren") == []


async def test_screenshots_returns_nothing_when_the_data_container_is_missing() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        assert await jikan.screenshots(client, 52991) == []


async def test_get_by_id_returns_none_when_the_detail_body_has_no_entry() -> None:
    """Jikan wraps its single detail entry in "data"; a body without one
    is reported as "gone" rather than raising past every handler."""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"data": None})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        assert await jikan.get_by_id(client, 52991) is None


async def test_screenshots_is_cached_for_repeated_calls() -> None:
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        return httpx.Response(200, json={"data": []})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        await jikan.screenshots(client, 52991)
        await jikan.screenshots(client, 52991)

    assert calls["n"] == 1


async def test_search_skips_an_entry_with_no_mal_id() -> None:
    """A third party controls these keys: one entry missing the one field
    the parser indexes must not cost the starter the other results
    (issue #83)."""
    entries = [{"title": "No id here"}, {"mal_id": 52991, "title": "Sousou no Frieren"}]

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"data": entries})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        results = await jikan.search(client, "frieren")

    assert [result.jikan_id for result in results] == [52991]


async def test_search_skips_scalar_entries() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"data": [1, 2]})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        results = await jikan.search(client, "frieren")

    assert results == []


async def test_search_raises_when_the_data_container_is_not_an_array() -> None:
    """A wholly-unusable container is an outage, not zero results — and
    iterating a number is a TypeError no handler catches."""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"data": 5})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(RuntimeError, match="expected an array"):
            await jikan.search(client, "frieren")


async def test_get_by_id_returns_none_when_the_entry_has_no_mal_id() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"data": {"title": "Sousou no Frieren"}})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        result = await jikan.get_by_id(client, 52991)

    assert result is None


async def test_screenshots_skips_scalar_entries() -> None:
    """entry.get("jpg") on an int is an AttributeError."""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"data": [1, {"jpg": {"image_url": "a.jpg"}}]})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        urls = await jikan.screenshots(client, 52991)

    assert urls == ["a.jpg"]


async def test_search_skips_an_entry_whose_id_is_null() -> None:
    """`raw["mal_id"]` succeeds for a JSON null, so the missing-key guard
    alone would stage a result with jikan_id=None and build a
    `jikan_pick:None` button that cannot work (issue #83)."""
    entries = [{"mal_id": None, "title": "Null id"}, {"mal_id": 52991, "title": "Frieren"}]

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"data": entries})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        results = await jikan.search(client, "frieren")

    assert [result.jikan_id for result in results] == [52991]


async def test_get_by_id_returns_none_when_the_id_is_null() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"data": {"mal_id": None, "title": "Frieren"}})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        result = await jikan.get_by_id(client, 52991)

    assert result is None


_GOOD_ENTRY = {
    "mal_id": 52991,
    "title": "Sousou no Frieren",
    "title_english": "Frieren: Beyond Journey's End",
    "title_japanese": "葬送のフリーレン",
    "title_synonyms": ["Frieren at the Funeral"],
}


@pytest.mark.parametrize(
    "field",
    [
        pytest.param({"title": 5}, id="title-is-a-number"),
        pytest.param({"title_english": ["Frieren"]}, id="english-is-an-array"),
        pytest.param({"title_japanese": {"jp": "x"}}, id="japanese-is-an-object"),
        pytest.param({"title_synonyms": 5}, id="synonyms-is-a-number"),
        pytest.param({"title_synonyms": "Frieren"}, id="synonyms-is-a-string"),
        pytest.param({"title_synonyms": ["Frieren", 5]}, id="synonyms-holds-a-number"),
        pytest.param({"title_synonyms": {"0": "Frieren"}}, id="synonyms-is-an-object"),
    ],
)
async def test_search_skips_an_entry_with_a_malformed_field(field: dict) -> None:
    """Same rule as every other provider: a field the parser can't hand
    back usably takes the entry with it (issue #86)."""
    bad = {**_GOOD_ENTRY, "mal_id": 1, **field}

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"data": [bad, _GOOD_ENTRY]})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        results = await jikan.search(client, "frieren")

    assert [result.jikan_id for result in results] == [52991]


async def test_search_never_expands_a_string_synonyms_into_characters() -> None:
    entry = {**_GOOD_ENTRY, "title_synonyms": "Frieren"}

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"data": [entry]})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        results = await jikan.search(client, "frieren")

    assert results == []


@pytest.mark.parametrize(
    "field",
    [
        pytest.param({"title": 5}, id="title-is-a-number"),
        pytest.param({"title_synonyms": "Frieren"}, id="synonyms-is-a-string"),
        pytest.param({"title_synonyms": [5]}, id="synonyms-holds-a-number"),
    ],
)
async def test_get_by_id_returns_none_for_a_malformed_field(field: dict) -> None:
    entry = {**_GOOD_ENTRY, **field}

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"data": entry})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        assert await jikan.get_by_id(client, 52991) is None


# issue #89: a well-typed entry whose titles are all null/absent/empty and
# whose synonyms are also empty stages an unwinnable game (an empty
# match_candidates() list) that looks exactly like a working one. Explicit
# per-case entries rather than merging over _GOOD_ENTRY: the "absent
# entirely" case needs the keys gone, not present-and-null, which a dict
# merge over an entry that already has those keys set can't express.
_NO_TITLE_NO_SYNONYMS_ENTRIES = [
    pytest.param(
        {
            "mal_id": 1,
            "title": None,
            "title_english": None,
            "title_japanese": None,
            "title_synonyms": [],
        },
        id="all-titles-null",
    ),
    pytest.param(
        {
            "mal_id": 1,
            "title": "",
            "title_english": "",
            "title_japanese": "",
            "title_synonyms": [],
        },
        id="all-titles-empty-string",
    ),
    pytest.param({"mal_id": 1}, id="titles-absent-entirely"),
    pytest.param(
        {
            "mal_id": 1,
            "title": None,
            "title_english": None,
            "title_japanese": None,
            "title_synonyms": ["", ""],
        },
        id="synonyms-nonempty-list-of-only-empty-strings",
    ),
]


@pytest.mark.parametrize("bad", _NO_TITLE_NO_SYNONYMS_ENTRIES)
async def test_search_skips_an_entry_with_no_title_and_no_synonyms(bad: dict) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"data": [bad, _GOOD_ENTRY]})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        results = await jikan.search(client, "frieren")

    assert [result.jikan_id for result in results] == [52991]


@pytest.mark.parametrize("entry", _NO_TITLE_NO_SYNONYMS_ENTRIES)
async def test_get_by_id_returns_none_when_the_entry_has_no_title_and_no_synonyms(
    entry: dict,
) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"data": entry})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        assert await jikan.get_by_id(client, 52991) is None


async def test_search_keeps_an_entry_with_no_title_but_nonempty_synonyms() -> None:
    """Design decision (see parsing.has_answer_key): synonyms alone are
    still a real answer key, even though the picker button reads "?"."""
    entry = {
        "mal_id": 1,
        "title": None,
        "title_english": None,
        "title_japanese": None,
        "title_synonyms": ["Frieren"],
    }

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"data": [entry]})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        results = await jikan.search(client, "frieren")

    assert results == [
        jikan.JikanResult(
            jikan_id=1,
            title_romaji=None,
            title_english=None,
            title_native=None,
            synonyms=["Frieren"],
        )
    ]


async def test_get_by_id_keeps_an_entry_with_no_title_but_nonempty_synonyms() -> None:
    entry = {
        "mal_id": 1,
        "title": None,
        "title_english": None,
        "title_japanese": None,
        "title_synonyms": ["Frieren"],
    }

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"data": entry})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        result = await jikan.get_by_id(client, 1)

    assert result == jikan.JikanResult(
        jikan_id=1,
        title_romaji=None,
        title_english=None,
        title_native=None,
        synonyms=["Frieren"],
    )


async def test_search_keeps_a_legitimately_sparse_entry_with_one_title_variant() -> None:
    """Regression guard: one populated title variant, the rest null, is
    winnable and must not be caught by the no-title check."""
    entry = {"mal_id": 1, "title": "Some Anime", "title_english": None, "title_japanese": None}

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"data": [entry]})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        results = await jikan.search(client, "frieren")

    assert [result.jikan_id for result in results] == [1]


async def test_get_by_id_warns_naming_the_parser_when_a_field_is_malformed(
    records: list[tuple[str, str]],
) -> None:
    entry = {**_GOOD_ENTRY, "title_synonyms": "Frieren"}

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"data": entry})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        await jikan.get_by_id(client, 52991)

    warnings = [message for level, message in records if level == "WARNING"]
    assert len(warnings) == 1
    assert "Jikan" in warnings[0]
    assert "title_synonyms" in warnings[0]


@pytest.mark.parametrize(
    "jpg",
    [
        pytest.param({"large_image_url": 5}, id="large-is-a-number"),
        pytest.param({"large_image_url": {"url": "a.jpg"}}, id="large-is-an-object"),
        pytest.param({"image_url": 5}, id="fallback-is-a-number"),
        pytest.param({"large_image_url": None, "image_url": ["a.jpg"]}, id="fallback-is-an-array"),
    ],
)
async def test_screenshots_skips_a_picture_whose_url_is_not_a_string(jpg: dict) -> None:
    """`_picture_url` is declared `str | None` and its result goes
    straight to `InputMediaPhoto(media=url)` — a bare int reached it
    intact (issue #86)."""
    entries = [{"jpg": jpg, "webp": {}}, {"jpg": {"large_image_url": "a-large.jpg"}, "webp": {}}]

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"data": entries})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        urls = await jikan.screenshots(client, 52991)

    assert urls == ["a-large.jpg"]


@pytest.mark.parametrize(
    "jpg",
    [
        pytest.param(
            {"large_image_url": "a-large.jpg", "image_url": 5}, id="unused-fallback-is-a-number"
        ),
        pytest.param({"large_image_url": 5, "image_url": "a.jpg"}, id="preferred-is-a-number"),
    ],
)
async def test_screenshots_treats_both_url_fields_alike(
    jpg: dict, records: list[tuple[str, str]]
) -> None:
    """Whichever of the two is malformed, the picture goes — the
    short-circuiting `or` used to check only the preferred field, so an
    entry was kept or skipped depending on which half a provider had
    mistyped rather than on whether it was mistyped at all.

    Both alike rather than "fall back past the bad one" because that is
    exactly what `_parse_result` already does for a malformed
    `title_japanese` when `title` is fine: the entry is skipped, not
    salvaged field by field (issue #86's review)."""
    entries = [{"jpg": jpg, "webp": {}}, {"jpg": {"large_image_url": "b-large.jpg"}, "webp": {}}]

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"data": entries})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        urls = await jikan.screenshots(client, 52991)

    assert urls == ["b-large.jpg"]
    warnings = [message for level, message in records if level == "WARNING"]
    assert len(warnings) == 1
    assert "_picture_url" in warnings[0]


JIKAN_RANDOM_URL = "https://api.jikan.moe/v4/random/anime"


async def test_random_anime_hits_the_random_endpoint_and_parses_the_result() -> None:
    entry = {
        "mal_id": 52991,
        "title": "Sousou no Frieren",
        "title_english": "Frieren: Beyond Journey's End",
        "title_japanese": "葬送のフリーレン",
        "title_synonyms": ["Frieren at the Funeral"],
    }
    captured: dict[str, str] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["url"] = str(request.url).split("?")[0]
        return httpx.Response(200, json={"data": entry})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        result = await jikan.random_anime(client)

    assert captured["url"] == JIKAN_RANDOM_URL
    assert result == jikan.JikanResult(
        jikan_id=52991,
        title_romaji="Sousou no Frieren",
        title_english="Frieren: Beyond Journey's End",
        title_native="葬送のフリーレン",
        synonyms=["Frieren at the Funeral"],
    )


async def test_random_anime_returns_none_when_the_data_container_is_null() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"data": None})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        assert await jikan.random_anime(client) is None


async def test_random_anime_is_not_cached_across_calls() -> None:
    """See shikimori.py's identical test — a cached "random" pick would
    return the same anime every call, defeating both genuine randomness
    and services/game/autostart.py's retry-a-different-anime loop."""
    calls = {"n": 0}
    entries = [
        {"mal_id": 1, "title": "First"},
        {"mal_id": 2, "title": "Second"},
    ]

    def handler(request: httpx.Request) -> httpx.Response:
        entry = entries[calls["n"]]
        calls["n"] += 1
        return httpx.Response(200, json={"data": entry})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        first = await jikan.random_anime(client)
        second = await jikan.random_anime(client)

    assert calls["n"] == 2
    assert first is not None
    assert second is not None
    assert first.jikan_id != second.jikan_id
