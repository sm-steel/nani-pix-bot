import json
from typing import Any

import httpx
import pytest

from nani_pix_bot.services.search import cache, shikimori


@pytest.fixture(autouse=True)
def _clear_cache():
    cache.clear()
    yield
    cache.clear()


def _animes_payload(entries: list[Any]) -> dict:
    return {"data": {"animes": entries}}


def _responding(body: Any) -> httpx.MockTransport:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=body)

    return httpx.MockTransport(handler)


async def test_search_parses_a_result() -> None:
    entry = {
        "id": "52991",
        "name": "Sousou no Frieren",
        "russian": "Провожающая в последний путь Фрирен",
    }

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=_animes_payload([entry]))

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
    entry = {"id": "1", "name": "Some Anime", "russian": None}

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=_animes_payload([entry]))

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
        return httpx.Response(200, json=_animes_payload([]))

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        await shikimori.search(client, "frieren")

    assert captured["user_agent"] == shikimori._REQUEST_HEADERS["User-Agent"]


async def test_search_sends_the_query_and_limit_as_graphql_variables() -> None:
    """Request-assertion targets move from URL params (REST) to the POST
    body's query/variables (GraphQL) — this pins the wiring so a typo in
    the variable names fails loudly instead of silently searching for
    nothing."""
    captured: dict[str, Any] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["url"] = str(request.url)
        captured["json"] = json.loads(request.content)
        return httpx.Response(200, json=_animes_payload([]))

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        await shikimori.search(client, "frieren", limit=3)

    assert captured["url"] == shikimori.SHIKIMORI_GRAPHQL_URL
    assert captured["json"]["variables"] == {"search": "frieren", "limit": 3}
    assert "animes" in captured["json"]["query"]


async def test_get_by_id_sends_the_id_as_a_string_variable() -> None:
    """Shikimori's `ids` argument is typed String, confirmed live as
    `animes(ids: "817")` — the int id this module's callers pass in must
    be stringified before it goes on the wire."""
    captured: dict[str, Any] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["json"] = json.loads(request.content)
        return httpx.Response(200, json=_animes_payload([]))

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        await shikimori.get_by_id(client, 52991)

    assert captured["json"]["variables"] == {"ids": "52991"}


async def test_screenshots_sends_the_id_as_a_string_variable() -> None:
    """Mirrors test_get_by_id_sends_the_id_as_a_string_variable: the same
    `ids` stringification requirement applies to the screenshots query."""
    captured: dict[str, Any] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["json"] = json.loads(request.content)
        return httpx.Response(200, json=_animes_payload([]))

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        await shikimori.screenshots(client, 52991)

    assert captured["json"]["variables"] == {"ids": "52991"}


async def test_search_retries_after_rate_limit_then_succeeds() -> None:
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        if calls["n"] == 1:
            return httpx.Response(429, headers={"Retry-After": "0"})
        return httpx.Response(200, json=_animes_payload([]))

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
        "id": "52991",
        "name": "Sousou no Frieren",
        "russian": "Провожающая в последний путь Фрирен",
        "english": "Frieren: Beyond Journey's End",
        "synonyms": ["Фрирен, провожающая в последний путь", "Frieren at the Funeral"],
    }

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=_animes_payload([entry]))

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
    entry = {"id": "1", "name": "Some Anime", "russian": None}

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=_animes_payload([entry]))

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
    """Confirmed live contract: an unknown id answers `{"data":
    {"animes": []}}`, HTTP 200, no `errors` — not REST's 404 status,
    which this test used to mock."""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=_animes_payload([]))

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        result = await shikimori.get_by_id(client, 999999)

    assert result is None


async def test_search_is_cached_for_repeated_identical_queries() -> None:
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        return httpx.Response(200, json=_animes_payload([]))

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        await shikimori.search(client, "frieren")
        await shikimori.search(client, "frieren")

    assert calls["n"] == 1


async def test_get_by_id_is_cached_for_repeated_identical_ids() -> None:
    entry = {"id": "52991", "name": "Sousou no Frieren", "russian": None}
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        return httpx.Response(200, json=_animes_payload([entry]))

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        await shikimori.get_by_id(client, 52991)
        await shikimori.get_by_id(client, 52991)

    assert calls["n"] == 1


async def test_screenshots_parses_absolute_urls() -> None:
    """GraphQL's `originalUrl` is already absolute (confirmed live) —
    unlike REST's `original`, a host-relative path this module used to
    prefix with SHIKIMORI_HOST by hand."""
    entries = [
        {"originalUrl": "https://shikimori.io/system/screenshots/original/a.jpg?1"},
        {"originalUrl": "https://shikimori.io/system/screenshots/original/b.jpg?2"},
    ]

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=_animes_payload([{"screenshots": entries}]))

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        urls = await shikimori.screenshots(client, 52991)

    assert urls == [
        "https://shikimori.io/system/screenshots/original/a.jpg?1",
        "https://shikimori.io/system/screenshots/original/b.jpg?2",
    ]


async def test_screenshots_caps_at_the_fetch_limit() -> None:
    entries = [{"originalUrl": f"https://shikimori.io/x/{i}.jpg"} for i in range(30)]

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=_animes_payload([{"screenshots": entries}]))

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        urls = await shikimori.screenshots(client, 52991)

    assert len(urls) == shikimori.SCREENSHOT_FETCH_LIMIT


async def test_screenshots_returns_empty_list_when_none_exist() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=_animes_payload([{"screenshots": []}]))

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        urls = await shikimori.screenshots(client, 1)

    assert urls == []


async def test_screenshots_returns_empty_list_when_the_anime_is_not_found() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=_animes_payload([]))

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        urls = await shikimori.screenshots(client, 999999)

    assert urls == []


async def test_search_raises_a_runtime_error_on_a_non_json_body() -> None:
    """Shikimori is reached over a proxy, which can answer 200 with its
    own HTML error page — that must surface as a RuntimeError the
    handlers already catch (issue #75)."""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text="<html>Bad gateway</html>")

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(RuntimeError, match="non-JSON"):
            await shikimori.search(client, "frieren")


async def test_search_raises_a_runtime_error_on_a_literal_null_body() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text="null", headers={"Content-Type": "application/json"})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(RuntimeError, match="answered 200"):
            await shikimori.search(client, "frieren")


async def test_screenshots_raises_when_the_screenshots_field_is_not_an_array() -> None:
    """`screenshots` is declared as a list field, so an error object
    where the array should be is an outage, not zero screenshots."""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200, json=_animes_payload([{"screenshots": {"message": "something went wrong"}}])
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(RuntimeError, match="expected an array"):
            await shikimori.screenshots(client, 52991)


async def test_screenshots_skips_entries_with_no_original_url() -> None:
    entries = [
        {"originalUrl": "https://shikimori.io/x/a.jpg?1"},
        {"other": "https://shikimori.io/x/b.jpg?2"},
        {"originalUrl": None},
    ]

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=_animes_payload([{"screenshots": entries}]))

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        urls = await shikimori.screenshots(client, 52991)

    assert urls == ["https://shikimori.io/x/a.jpg?1"]


async def test_screenshots_is_cached_for_repeated_calls() -> None:
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        return httpx.Response(200, json=_animes_payload([{"screenshots": []}]))

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        await shikimori.screenshots(client, 52991)
        await shikimori.screenshots(client, 52991)

    assert calls["n"] == 1


async def test_search_skips_an_entry_with_no_id() -> None:
    """A third party controls these keys: one entry missing the one field
    the parser indexes must not cost the starter the other results
    (issue #83)."""
    entries = [{"name": "No id here"}, {"id": "52991", "name": "Sousou no Frieren"}]

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=_animes_payload(entries))

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        results = await shikimori.search(client, "frieren")

    assert [result.shikimori_id for result in results] == [52991]


async def test_search_skips_scalar_entries() -> None:
    """An array of scalars where an array of objects belongs — a plain
    dict-indexing on an int is a TypeError no handler catches."""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=_animes_payload([1, 2]))

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        results = await shikimori.search(client, "frieren")

    assert results == []


async def test_get_by_id_raises_when_animes_is_not_a_list() -> None:
    """The container guard search() already has via parsing.parse_entries
    must apply to get_by_id too — a truthy non-list `animes` (a dict here)
    is an outage, not a vanished pick, and must reach
    _SEARCH_SERVICE_ERRORS as a RuntimeError rather than raising an
    uncaught KeyError/TypeError from unguarded indexing (issue #83,
    reintroduced for this call shape)."""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"data": {"animes": {"message": "unexpected"}}})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(RuntimeError, match="expected an array"):
            await shikimori.get_by_id(client, 52991)


async def test_screenshots_raises_when_animes_is_not_a_list() -> None:
    """The screenshots equivalent of test_get_by_id_raises_when_animes_is_not_a_list."""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"data": {"animes": {"message": "unexpected"}}})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(RuntimeError, match="expected an array"):
            await shikimori.screenshots(client, 52991)


async def test_screenshots_skips_scalar_anime_entries() -> None:
    """The screenshots equivalent of test_search_skips_scalar_entries: an
    `animes` array of scalars where an array of entry objects belongs
    must be skipped per-entry rather than indexed unguarded."""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=_animes_payload([1, 2]))

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        urls = await shikimori.screenshots(client, 52991)

    assert urls == []


async def test_get_by_id_returns_none_when_the_entry_has_no_id() -> None:
    """Nothing usable came back for this pick, which the picker already
    reports the same way it reports a not-found result."""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=_animes_payload([{"name": "Sousou no Frieren"}]))

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        result = await shikimori.get_by_id(client, 52991)

    assert result is None


async def test_screenshots_skips_scalar_entries() -> None:
    """`entry.get("originalUrl")` on an int is an AttributeError — the
    inline comprehension needs the same guard the parse functions get."""
    entries = [1, {"originalUrl": "https://shikimori.io/x/a.jpg"}]

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=_animes_payload([{"screenshots": entries}]))

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        urls = await shikimori.screenshots(client, 52991)

    assert urls == ["https://shikimori.io/x/a.jpg"]


async def test_search_skips_an_entry_whose_id_is_null() -> None:
    """A JSON null `id` must not stage a result with shikimori_id=None
    and build a `shikimori_pick:None` button that cannot work
    (issue #83)."""
    entries = [{"id": None, "name": "Null id"}, {"id": "52991", "name": "Sousou no Frieren"}]

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=_animes_payload(entries))

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        results = await shikimori.search(client, "frieren")

    assert [result.shikimori_id for result in results] == [52991]


@pytest.mark.parametrize(
    "raw_id",
    [
        # A plain JSON int is *not* the confirmed shape here, unlike
        # AniList's Media.id: Shikimori's GraphQL `Anime.id` is a GraphQL
        # `ID` scalar, confirmed live to serialize as a numeric *string*
        # ("id": "52991") for both `animes(search: ...)` and
        # `animes(ids: ...)`. An int id is exactly as wrong as any other
        # shape here.
        pytest.param(52991, id="id-is-an-int"),
        pytest.param(52991.0, id="id-is-a-float"),
        pytest.param(True, id="id-is-a-bool"),
        pytest.param(["52991"], id="id-is-an-array"),
        pytest.param({"value": "52991"}, id="id-is-an-object"),
        pytest.param("52991a", id="id-is-a-non-numeric-string"),
        pytest.param("", id="id-is-an-empty-string"),
        # "²".isdigit() is True but int("²") raises ValueError, which
        # isn't in parsing._MALFORMED_ENTRY_ERRORS and would escape
        # uncaught if isdigit() were trusted alone — must be rejected by
        # the isascii() check before int() is ever called.
        pytest.param("²", id="id-is-a-non-ascii-digit"),
    ],
)
async def test_search_skips_an_entry_with_a_malformed_id(raw_id: object) -> None:
    entries = [{"id": raw_id, "name": "Bad id"}, {"id": "1", "name": "Some Anime"}]

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=_animes_payload(entries))

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        results = await shikimori.search(client, "frieren")

    assert [result.shikimori_id for result in results] == [1]


async def test_get_by_id_returns_none_when_the_id_is_null() -> None:
    entry = {"id": None, "name": "Sousou no Frieren"}

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=_animes_payload([entry]))

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        result = await shikimori.get_by_id(client, 52991)

    assert result is None


_GOOD_SEARCH_ENTRY = {"id": "52991", "name": "Sousou no Frieren", "russian": "Фрирен"}
_GOOD_DETAIL_ENTRY = {
    "id": "52991",
    "name": "Sousou no Frieren",
    "russian": "Фрирен",
    "english": "Frieren: Beyond Journey's End",
    "synonyms": ["Frieren at the Funeral"],
}


@pytest.mark.parametrize(
    "field",
    [
        pytest.param({"name": 5}, id="name-is-a-number"),
        pytest.param({"name": ["Frieren"]}, id="name-is-an-array"),
        pytest.param({"russian": {"ru": "Фрирен"}}, id="russian-is-an-object"),
        pytest.param({"russian": True}, id="russian-is-a-bool"),
    ],
)
async def test_search_skips_an_entry_with_a_malformed_title(field: dict) -> None:
    """A non-string title reaches `", ".join(...)` in the setup preview.
    The entry goes whole rather than keeping its id and dropping the
    field: an all-None title set renders as "?" (issue #86)."""
    bad = {**_GOOD_SEARCH_ENTRY, "id": "1", **field}
    body = _animes_payload([bad, _GOOD_SEARCH_ENTRY])

    async with httpx.AsyncClient(transport=_responding(body)) as c:
        results = await shikimori.search(c, "frieren")

    assert [result.shikimori_id for result in results] == [52991]


@pytest.mark.parametrize(
    "field",
    [
        pytest.param({"synonyms": 5}, id="synonyms-is-a-number"),
        pytest.param({"synonyms": "Frieren"}, id="synonyms-is-a-string"),
        pytest.param({"synonyms": ["Frieren", 5]}, id="synonyms-holds-a-number"),
        pytest.param({"synonyms": {"0": "Frieren"}}, id="synonyms-is-an-object"),
        pytest.param({"english": 5}, id="english-is-a-number"),
        pytest.param({"english": ["Frieren"]}, id="english-is-an-array"),
        pytest.param({"english": {"0": "Frieren"}}, id="english-is-an-object"),
        pytest.param({"english": True}, id="english-is-a-bool"),
        pytest.param({"name": 5}, id="name-is-a-number"),
    ],
)
async def test_get_by_id_returns_none_for_a_malformed_field(field: dict) -> None:
    """The detail query is the only one that returns synonyms/english,
    so it is where the answer key actually gets populated. Unlike REST's
    `english: [None | str]` shape, GraphQL's `Anime.english` is a plain
    nullable String scalar (the fix for issue #103) — so a bare string
    `english` is no longer a malformed shape at all, it's the norm; what
    replaces it here are the shapes a String scalar genuinely can't be."""
    entry = {**_GOOD_DETAIL_ENTRY, **field}

    async with httpx.AsyncClient(transport=_responding(_animes_payload([entry]))) as client:
        assert await shikimori.get_by_id(client, 52991) is None


async def test_get_by_id_never_expands_a_string_synonyms_into_characters() -> None:
    entry = {**_GOOD_DETAIL_ENTRY, "synonyms": "Frieren"}

    async with httpx.AsyncClient(transport=_responding(_animes_payload([entry]))) as client:
        assert await shikimori.get_by_id(client, 52991) is None


async def test_get_by_id_warns_naming_the_parser_when_a_field_is_malformed(
    records: list[tuple[str, str]],
) -> None:
    entry = {**_GOOD_DETAIL_ENTRY, "synonyms": "Frieren"}

    async with httpx.AsyncClient(transport=_responding(_animes_payload([entry]))) as client:
        await shikimori.get_by_id(client, 52991)

    warnings = [message for level, message in records if level == "WARNING"]
    assert len(warnings) == 1
    assert "Shikimori" in warnings[0]
    assert "_parse_detail_result" in warnings[0]
    assert "synonyms" in warnings[0]


@pytest.mark.parametrize(
    "original_url",
    [
        pytest.param({"a": 1}, id="original-url-is-an-object"),
        pytest.param(5, id="original-url-is-a-number"),
        pytest.param(["/x/a.jpg"], id="original-url-is-an-array"),
        pytest.param(True, id="original-url-is-a-bool"),
    ],
)
async def test_screenshots_skips_an_entry_whose_original_url_is_not_a_string(
    original_url: object,
) -> None:
    entries = [{"originalUrl": original_url}, {"originalUrl": "https://shikimori.io/x/a.jpg"}]

    async with httpx.AsyncClient(
        transport=_responding(_animes_payload([{"screenshots": entries}]))
    ) as client:
        urls = await shikimori.screenshots(client, 52991)

    assert urls == ["https://shikimori.io/x/a.jpg"]


async def test_search_raises_when_shikimori_reports_graphql_errors_with_no_data() -> None:
    """A GraphQL error response with a null `data` is an explicit "I am
    broken" signal, not an empty result — has to reach
    _SEARCH_SERVICE_ERRORS as a RuntimeError, not "nothing found"."""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"data": None, "errors": [{"message": "boom"}]})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(RuntimeError, match="GraphQL error"):
            await shikimori.search(client, "frieren")


# issue #89: a well-typed entry whose titles are all null/absent/empty
# stages an unwinnable game (an empty match_candidates() list) that looks
# exactly like a working one. The search endpoint never carries
# synonyms/english (see the module docstring), so at search time this
# reduces to "skip when both name and russian are empty" — the
# "no title but has synonyms survives" half of the design decision can
# only be exercised at get_by_id, the only call that ever sees synonyms.
_NO_TITLE_SEARCH_ENTRIES = [
    pytest.param({"id": "1", "name": None, "russian": None}, id="all-titles-null"),
    pytest.param({"id": "1", "name": "", "russian": ""}, id="all-titles-empty-string"),
    pytest.param({"id": "1"}, id="titles-absent-entirely"),
]


@pytest.mark.parametrize("bad", _NO_TITLE_SEARCH_ENTRIES)
async def test_search_skips_an_entry_with_no_title(bad: dict) -> None:
    good = {"id": "52991", "name": "Sousou no Frieren", "russian": "Фрирен"}

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=_animes_payload([bad, good]))

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        results = await shikimori.search(client, "frieren")

    assert [result.shikimori_id for result in results] == [52991]


async def test_search_keeps_a_legitimately_sparse_entry_with_one_title_variant() -> None:
    """Regression guard: one populated title variant, the rest null, is
    winnable and must not be caught by the no-title check."""
    entry = {"id": "1", "name": "Some Anime", "russian": None}

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=_animes_payload([entry]))

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        results = await shikimori.search(client, "frieren")

    assert [result.shikimori_id for result in results] == [1]


_NO_TITLE_NO_SYNONYMS_DETAIL_ENTRIES = [
    pytest.param(
        {"id": "1", "name": None, "russian": None, "english": None, "synonyms": []},
        id="all-titles-null",
    ),
    pytest.param(
        {"id": "1", "name": "", "russian": "", "english": "", "synonyms": []},
        id="all-titles-empty-string",
    ),
    pytest.param({"id": "1"}, id="titles-absent-entirely"),
    pytest.param(
        {"id": "1", "name": None, "russian": None, "english": None, "synonyms": ["", ""]},
        id="synonyms-nonempty-list-of-only-empty-strings",
    ),
]


@pytest.mark.parametrize("entry", _NO_TITLE_NO_SYNONYMS_DETAIL_ENTRIES)
async def test_get_by_id_returns_none_when_the_entry_has_no_title_and_no_synonyms(
    entry: dict,
) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=_animes_payload([entry]))

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        assert await shikimori.get_by_id(client, 1) is None


async def test_get_by_id_keeps_an_entry_with_no_title_but_nonempty_synonyms() -> None:
    """Design decision (see parsing.has_answer_key): synonyms alone are
    still a real answer key, even though the picker button reads "?"."""
    entry = {
        "id": "1",
        "name": None,
        "russian": None,
        "english": None,
        "synonyms": ["Frieren"],
    }

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=_animes_payload([entry]))

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        result = await shikimori.get_by_id(client, 1)

    assert result == shikimori.ShikimoriResult(
        shikimori_id=1,
        title_romaji=None,
        title_english=None,
        title_russian=None,
        synonyms=["Frieren"],
    )


async def test_get_by_id_keeps_a_legitimately_sparse_entry_with_one_title_variant() -> None:
    """Regression guard: one populated title variant, the rest null/no
    synonyms, is winnable and must not be caught by the no-title check."""
    entry = {"id": "1", "name": "Some Anime", "russian": None, "english": None, "synonyms": []}

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=_animes_payload([entry]))

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        result = await shikimori.get_by_id(client, 1)

    assert result == shikimori.ShikimoriResult(
        shikimori_id=1,
        title_romaji="Some Anime",
        title_english=None,
        title_russian=None,
        synonyms=[],
    )


async def test_search_logs_graphql_errors_even_when_data_came_back(monkeypatch) -> None:
    """GraphQL allows partial success — data alongside errors. The usable
    half is still used, but the errors never go unrecorded."""
    entry = {"id": "1", "name": "Some Anime", "russian": None}
    logged: list[tuple] = []
    monkeypatch.setattr(shikimori.logger, "error", lambda *args: logged.append(args))

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200, json={"data": {"animes": [entry]}, "errors": [{"message": "deprecated"}]}
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        results = await shikimori.search(client, "some anime")

    assert [r.shikimori_id for r in results] == [1]
    assert len(logged) == 1
    assert "deprecated" in str(logged[0])


async def test_random_anime_sends_a_random_order_query() -> None:
    captured: dict[str, Any] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["json"] = json.loads(request.content)
        entry = {
            "id": "1",
            "name": "Some Anime",
            "russian": None,
            "english": "Some Anime",
            "synonyms": [],
        }
        return httpx.Response(200, json=_animes_payload([entry]))

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        result = await shikimori.random_anime(client)

    assert "order" in captured["json"]["query"]
    assert "random" in captured["json"]["query"].lower()
    assert result == shikimori.ShikimoriResult(
        shikimori_id=1,
        title_romaji="Some Anime",
        title_english="Some Anime",
        title_russian=None,
        synonyms=[],
    )


async def test_random_anime_returns_none_when_nothing_comes_back() -> None:
    async with httpx.AsyncClient(transport=_responding(_animes_payload([]))) as client:
        result = await shikimori.random_anime(client)

    assert result is None


async def test_random_anime_is_not_cached_across_calls() -> None:
    """Unlike search()/get_by_id()/screenshots(), random_anime() must
    genuinely re-hit the API on every call — @cache.cached() would
    return the same "random" result for DEFAULT_TTL_SECONDS, defeating
    both randomness and services/game/autostart.py's retry-a-different-
    anime loop."""
    calls = {"n": 0}
    entries = [
        {"id": "1", "name": "First", "russian": None},
        {"id": "2", "name": "Second", "russian": None},
    ]

    def handler(request: httpx.Request) -> httpx.Response:
        entry = entries[calls["n"]]
        calls["n"] += 1
        return httpx.Response(200, json=_animes_payload([entry]))

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        first = await shikimori.random_anime(client)
        second = await shikimori.random_anime(client)

    assert calls["n"] == 2
    assert first is not None
    assert second is not None
    assert first.shikimori_id != second.shikimori_id
