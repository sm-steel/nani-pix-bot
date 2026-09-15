from typing import Any

import httpx
import pytest

from nani_pix_bot.services.search import anilist, cache


@pytest.fixture(autouse=True)
def _clear_cache():
    cache.clear()
    yield
    cache.clear()


def _media_payload(entries: list[Any]) -> dict:
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


async def test_search_skips_an_entry_with_no_id() -> None:
    """A third party controls these keys: one entry missing a field the
    parser indexes must not cost the starter the other results
    (issue #83)."""
    entries = [{"title": {"romaji": "No id here"}}, {"id": 154587, "title": {"romaji": "Frieren"}}]

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=_media_payload(entries))

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        results = await anilist.search(client, "frieren")

    assert [result.anilist_id for result in results] == [154587]


async def test_search_skips_an_entry_with_no_title_object() -> None:
    entries = [{"id": 1}, {"id": 154587, "title": {"romaji": "Frieren"}}]

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=_media_payload(entries))

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        results = await anilist.search(client, "frieren")

    assert [result.anilist_id for result in results] == [154587]


async def test_search_skips_an_entry_whose_title_is_a_scalar() -> None:
    """title.get("romaji") on a string is an AttributeError."""
    entries = [{"id": 1, "title": "Frieren"}, {"id": 154587, "title": {"romaji": "Frieren"}}]

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=_media_payload(entries))

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        results = await anilist.search(client, "frieren")

    assert [result.anilist_id for result in results] == [154587]


async def test_search_skips_scalar_entries() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=_media_payload([1, 2]))

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        results = await anilist.search(client, "frieren")

    assert results == []


async def test_search_raises_when_the_media_container_is_not_an_array() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"data": {"Page": {"media": 5}}})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(RuntimeError, match="expected an array"):
            await anilist.search(client, "frieren")


async def test_get_by_id_returns_none_when_the_media_entry_has_no_title() -> None:
    """Nothing usable came back for this pick, which the picker already
    reports the same way it reports a removed entry."""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"data": {"Media": {"id": 154587}}})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        result = await anilist.get_by_id(client, 154587)

    assert result is None


async def test_search_skips_an_entry_whose_id_is_null() -> None:
    """`raw["id"]` succeeds for a JSON null, so the missing-key guard alone
    would stage a result with anilist_id=None and build an
    `anilist_pick:None` button that cannot work (issue #83)."""
    entries = [
        {"id": None, "title": {"romaji": "Null id"}},
        {"id": 154587, "title": {"romaji": "Frieren"}},
    ]

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=_media_payload(entries))

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        results = await anilist.search(client, "frieren")

    assert [result.anilist_id for result in results] == [154587]


async def test_get_by_id_returns_none_when_the_id_is_null() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200, json={"data": {"Media": {"id": None, "title": {"romaji": "Frieren"}}}}
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        result = await anilist.get_by_id(client, 154587)

    assert result is None


def _responding(body: Any) -> httpx.MockTransport:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=body)

    return httpx.MockTransport(handler)


# AniList is the only provider that reads a container *nested inside* the
# body, so the `expect=` guard the REST three get from rest.get_json never
# covers it (issue #85). Two nested objects are read on the way to an
# entry — the `data` object itself, which both entry points go through,
# and the `Page` object inside it — and a scalar in either position used
# to escape as an AttributeError that _SEARCH_SERVICE_ERRORS doesn't
# catch. Probed as entry point x malformed shape rather than one case per
# shape: a fix at only one of the two reads leaves the other live.
_MALFORMED_DATA_BODIES = [
    pytest.param({"data": 5}, id="data-is-a-number"),
    pytest.param({"data": [1]}, id="data-is-an-array"),
    pytest.param({"data": "Page"}, id="data-is-a-string"),
    pytest.param({"data": True}, id="data-is-a-bool"),
    # Falsy, and therefore the half the pre-fix `data or {}` / `... or {}`
    # swallowed *silently*: the only line it emitted was a DEBUG "returned
    # 0 result(s)", making a dead provider indistinguishable from an
    # unpopular anime. The GraphQL spec allows `data` to be a map or null
    # and nothing else, so a falsy non-null here can only come from
    # something that isn't a GraphQL server — an interstitial, a proxy
    # envelope — and it has to raise like every other malformed shape.
    # Without these two params `or`-truthiness could be reintroduced in a
    # refactor with the suite staying green.
    pytest.param({"data": []}, id="data-is-an-empty-array"),
]


@pytest.mark.parametrize("body", _MALFORMED_DATA_BODIES)
async def test_search_raises_when_data_is_not_an_object(body: dict) -> None:
    async with httpx.AsyncClient(transport=_responding(body)) as client:
        with pytest.raises(RuntimeError, match="expected an object"):
            await anilist.search(client, "frieren")


@pytest.mark.parametrize("body", _MALFORMED_DATA_BODIES)
async def test_get_by_id_raises_when_data_is_not_an_object(body: dict) -> None:
    """The second site, and the easy one to miss: a scalar `data` blows up
    on the *first* nested read, before `Page`/`Media` is ever looked at."""
    async with httpx.AsyncClient(transport=_responding(body)) as client:
        with pytest.raises(RuntimeError, match="expected an object"):
            await anilist.get_by_id(client, 154587)


@pytest.mark.parametrize(
    "page",
    [
        pytest.param(5, id="page-is-a-number"),
        pytest.param([1], id="page-is-an-array"),
        pytest.param("media", id="page-is-a-string"),
        pytest.param(0, id="page-is-zero"),
    ],
)
async def test_search_raises_when_the_page_container_is_not_an_object(page: Any) -> None:
    async with httpx.AsyncClient(transport=_responding({"data": {"Page": page}})) as client:
        with pytest.raises(RuntimeError, match="expected an object"):
            await anilist.search(client, "frieren")


async def test_search_returns_nothing_when_the_page_container_is_null() -> None:
    """Absent is not malformed: a null container key keeps yielding the
    empty result the pickers already report as "nothing found"."""
    async with httpx.AsyncClient(transport=_responding({"data": {"Page": None}})) as client:
        assert await anilist.search(client, "frieren") == []


async def test_get_by_id_returns_none_when_the_media_entry_is_a_scalar() -> None:
    """`Media` is nested the same way `Page` is, but it is the *entry*, not
    a container of them — so it keeps issue #83's WARNING-and-skip, which
    the picker renders as "this pick is gone", rather than being pulled
    into the container guard and turned into an outage."""
    async with httpx.AsyncClient(transport=_responding({"data": {"Media": 5}})) as client:
        assert await anilist.get_by_id(client, 154587) is None


_GOOD_ENTRY = {
    "id": 154587,
    "title": {"romaji": "Sousou no Frieren", "english": None, "native": None},
    "synonyms": ["Frieren"],
    "startDate": {"year": 2023},
}


@pytest.mark.parametrize(
    "field",
    [
        pytest.param({"title": {"romaji": 5}}, id="romaji-is-a-number"),
        pytest.param({"title": {"english": ["Frieren"]}}, id="english-is-an-array"),
        pytest.param({"title": {"native": {"jp": "x"}}}, id="native-is-an-object"),
        pytest.param({"synonyms": 5}, id="synonyms-is-a-number"),
        pytest.param({"synonyms": "Frieren"}, id="synonyms-is-a-string"),
        pytest.param({"synonyms": ["Frieren", 5]}, id="synonyms-holds-a-number"),
        pytest.param({"synonyms": {"0": "Frieren"}}, id="synonyms-is-an-object"),
        pytest.param({"startDate": {"year": "2023"}}, id="year-is-a-string"),
    ],
)
async def test_search_skips_an_entry_with_a_malformed_field(field: dict) -> None:
    """A field the parser hands back unvalidated is outside the entry
    guard — it detonates at whatever consumes it instead (issue #86). The
    entry is skipped whole rather than kept with the bad field dropped:
    a result with no usable title stages a game whose answer key renders
    as "?"."""
    bad = {**_GOOD_ENTRY, **field}

    async with httpx.AsyncClient(transport=_responding(_media_payload([bad, _GOOD_ENTRY]))) as c:
        results = await anilist.search(c, "frieren")

    assert [result.anilist_id for result in results] == [154587]
    assert [result.synonyms for result in results] == [["Frieren"]]


async def test_search_never_expands_a_string_synonyms_into_characters() -> None:
    """The one defect in this family that doesn't raise: a string is
    iterable, so `{"synonyms": "Frieren"}` reached `match_candidates` as
    ['F','r','i','e','r','e','n'] and single letters became winning
    guesses — silently (issue #86)."""
    entry = {**_GOOD_ENTRY, "synonyms": "Frieren"}

    async with httpx.AsyncClient(transport=_responding(_media_payload([entry]))) as client:
        results = await anilist.search(client, "frieren")

    assert results == []


async def test_search_warns_naming_the_parser_when_a_field_is_malformed(
    records: list[tuple[str, str]],
) -> None:
    entry = {**_GOOD_ENTRY, "synonyms": "Frieren"}

    async with httpx.AsyncClient(transport=_responding(_media_payload([entry]))) as client:
        await anilist.search(client, "frieren")

    warnings = [message for level, message in records if level == "WARNING"]
    assert len(warnings) == 1
    assert "AniList" in warnings[0]
    assert "_parse_result" in warnings[0]
    assert "synonyms" in warnings[0]


@pytest.mark.parametrize(
    "field",
    [
        pytest.param({"title": {"romaji": 5}}, id="romaji-is-a-number"),
        pytest.param({"synonyms": "Frieren"}, id="synonyms-is-a-string"),
        pytest.param({"synonyms": [5]}, id="synonyms-holds-a-number"),
        pytest.param({"startDate": {"year": True}}, id="year-is-a-bool"),
    ],
)
async def test_get_by_id_returns_none_for_a_malformed_field(field: dict) -> None:
    """The by-id path has nothing left once its one entry is skipped —
    None, which the picker already reports as "this pick is gone"."""
    entry = {**_GOOD_ENTRY, **field}

    async with httpx.AsyncClient(transport=_responding({"data": {"Media": entry}})) as client:
        assert await anilist.get_by_id(client, 154587) is None
