import httpx
import pytest

from nani_pix_bot.services.search import cache, shikimori


@pytest.fixture(autouse=True)
def _clear_cache():
    cache.clear()
    yield
    cache.clear()


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


async def test_search_is_cached_for_repeated_identical_queries() -> None:
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        return httpx.Response(200, json=[])

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        await shikimori.search(client, "frieren")
        await shikimori.search(client, "frieren")

    assert calls["n"] == 1


async def test_get_by_id_is_cached_for_repeated_identical_ids() -> None:
    entry = {"id": 52991, "name": "Sousou no Frieren", "russian": None}
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        return httpx.Response(200, json=entry)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        await shikimori.get_by_id(client, 52991)
        await shikimori.get_by_id(client, 52991)

    assert calls["n"] == 1


async def test_screenshots_parses_and_prefixes_relative_urls() -> None:
    entries = [
        {"original": "/system/screenshots/original/a.jpg?1", "preview": "/x/a.jpg?1"},
        {"original": "/system/screenshots/original/b.jpg?2", "preview": "/x/b.jpg?2"},
    ]

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=entries)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        urls = await shikimori.screenshots(client, 52991)

    assert urls == [
        "https://shikimori.io/system/screenshots/original/a.jpg?1",
        "https://shikimori.io/system/screenshots/original/b.jpg?2",
    ]


async def test_screenshots_caps_at_the_fetch_limit() -> None:
    entries = [
        {"original": f"/system/screenshots/original/{i}.jpg", "preview": f"/x/{i}.jpg"}
        for i in range(30)
    ]

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=entries)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        urls = await shikimori.screenshots(client, 52991)

    assert len(urls) == shikimori.SCREENSHOT_FETCH_LIMIT


async def test_screenshots_returns_empty_list_when_none_exist() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=[])

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        urls = await shikimori.screenshots(client, 1)

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
    """Shikimori's list endpoints answer with a bare array; a 200 that
    decodes to `null` instead is not an empty list, and iterating it
    raises a TypeError no handler catches."""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text="null", headers={"Content-Type": "application/json"})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(RuntimeError, match="answered 200"):
            await shikimori.search(client, "frieren")


async def test_screenshots_raises_when_an_object_arrives_instead_of_an_array() -> None:
    """The screenshots endpoint is declared as a list endpoint, so an
    error object where the array should be is an outage, not zero
    screenshots."""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"message": "something went wrong"})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(RuntimeError, match="expected list"):
            await shikimori.screenshots(client, 52991)


async def test_screenshots_skips_entries_with_no_original_path() -> None:
    entries = [
        {"original": "/system/screenshots/original/a.jpg?1", "preview": "/x/a.jpg?1"},
        {"preview": "/x/b.jpg?2"},
        {"original": None, "preview": "/x/c.jpg?3"},
    ]

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=entries)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        urls = await shikimori.screenshots(client, 52991)

    assert urls == ["https://shikimori.io/system/screenshots/original/a.jpg?1"]


async def test_screenshots_is_cached_for_repeated_calls() -> None:
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        return httpx.Response(200, json=[])

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        await shikimori.screenshots(client, 52991)
        await shikimori.screenshots(client, 52991)

    assert calls["n"] == 1


async def test_search_skips_an_entry_with_no_id() -> None:
    """A third party controls these keys: one entry missing the one field
    the parser indexes must not cost the starter the other results
    (issue #83)."""
    entries = [{"name": "No id here"}, {"id": 52991, "name": "Sousou no Frieren"}]

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=entries)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        results = await shikimori.search(client, "frieren")

    assert [result.shikimori_id for result in results] == [52991]


async def test_search_skips_scalar_entries() -> None:
    """An array of scalars where an array of objects belongs — `raw["id"]`
    on an int is a TypeError no handler catches."""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=[1, 2])

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        results = await shikimori.search(client, "frieren")

    assert results == []


async def test_get_by_id_returns_none_when_the_entry_has_no_id() -> None:
    """Nothing usable came back for this pick, which the picker already
    reports the same way it reports a 404."""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"name": "Sousou no Frieren"})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        result = await shikimori.get_by_id(client, 52991)

    assert result is None


async def test_screenshots_skips_scalar_entries() -> None:
    """`entry.get("original")` on an int is an AttributeError — the
    inline comprehension needs the same guard the parse functions get."""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=[1, {"original": "/x/a.jpg"}])

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        urls = await shikimori.screenshots(client, 52991)

    assert urls == ["https://shikimori.io/x/a.jpg"]


async def test_search_skips_an_entry_whose_id_is_null() -> None:
    """`raw["id"]` succeeds for a JSON null, so the missing-key guard alone
    would stage a result with shikimori_id=None and build a
    `shikimori_pick:None` button that cannot work (issue #83)."""
    entries = [{"id": None, "name": "Null id"}, {"id": 52991, "name": "Sousou no Frieren"}]

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=entries)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        results = await shikimori.search(client, "frieren")

    assert [result.shikimori_id for result in results] == [52991]


async def test_search_skips_an_entry_whose_id_is_a_string() -> None:
    entries = [{"id": "52991", "name": "Stringly typed"}, {"id": 1, "name": "Some Anime"}]

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=entries)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        results = await shikimori.search(client, "frieren")

    assert [result.shikimori_id for result in results] == [1]


async def test_get_by_id_returns_none_when_the_id_is_null() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"id": None, "name": "Sousou no Frieren"})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        result = await shikimori.get_by_id(client, 52991)

    assert result is None


def _responding(body) -> httpx.MockTransport:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=body)

    return httpx.MockTransport(handler)


_GOOD_SEARCH_ENTRY = {"id": 52991, "name": "Sousou no Frieren", "russian": "Фрирен"}
_GOOD_DETAIL_ENTRY = {
    "id": 52991,
    "name": "Sousou no Frieren",
    "russian": "Фрирен",
    "english": ["Frieren: Beyond Journey's End"],
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
    bad = {**_GOOD_SEARCH_ENTRY, "id": 1, **field}

    async with httpx.AsyncClient(transport=_responding([bad, _GOOD_SEARCH_ENTRY])) as client:
        results = await shikimori.search(client, "frieren")

    assert [result.shikimori_id for result in results] == [52991]


@pytest.mark.parametrize(
    "field",
    [
        pytest.param({"synonyms": 5}, id="synonyms-is-a-number"),
        pytest.param({"synonyms": "Frieren"}, id="synonyms-is-a-string"),
        pytest.param({"synonyms": ["Frieren", 5]}, id="synonyms-holds-a-number"),
        pytest.param({"synonyms": {"0": "Frieren"}}, id="synonyms-is-an-object"),
        pytest.param({"english": "Frieren"}, id="english-is-a-string"),
        pytest.param({"english": [5]}, id="english-holds-a-number"),
        pytest.param({"english": 5}, id="english-is-a-number"),
        pytest.param({"english": {"0": "Frieren"}}, id="english-is-an-object"),
        pytest.param({"name": 5}, id="name-is-a-number"),
    ],
)
async def test_get_by_id_returns_none_for_a_malformed_field(field: dict) -> None:
    """The detail endpoint is the only one that returns synonyms/english,
    so it is where the answer key actually gets populated.

    `english` was the half-covered case: `english[0]` already raised
    inside the guard for a number (TypeError) and an object (KeyError),
    but a bare string sliced to its first *character* and a `[5]` handed
    back an int — neither raised, and both are exactly the corruption the
    indexing looked like it was preventing."""
    entry = {**_GOOD_DETAIL_ENTRY, **field}

    async with httpx.AsyncClient(transport=_responding(entry)) as client:
        assert await shikimori.get_by_id(client, 52991) is None


async def test_get_by_id_never_slices_a_string_english_to_one_character() -> None:
    """Pinned separately from the parametrize above because the old
    behaviour here wasn't a crash: it staged a game whose English title
    was "F"."""
    entry = {**_GOOD_DETAIL_ENTRY, "english": "Frieren"}

    async with httpx.AsyncClient(transport=_responding(entry)) as client:
        assert await shikimori.get_by_id(client, 52991) is None


async def test_get_by_id_never_expands_a_string_synonyms_into_characters() -> None:
    entry = {**_GOOD_DETAIL_ENTRY, "synonyms": "Frieren"}

    async with httpx.AsyncClient(transport=_responding(entry)) as client:
        assert await shikimori.get_by_id(client, 52991) is None


async def test_get_by_id_warns_naming_the_parser_when_a_field_is_malformed(
    records: list[tuple[str, str]],
) -> None:
    entry = {**_GOOD_DETAIL_ENTRY, "synonyms": "Frieren"}

    async with httpx.AsyncClient(transport=_responding(entry)) as client:
        await shikimori.get_by_id(client, 52991)

    warnings = [message for level, message in records if level == "WARNING"]
    assert len(warnings) == 1
    assert "Shikimori" in warnings[0]
    assert "_parse_detail_result" in warnings[0]
    assert "synonyms" in warnings[0]


@pytest.mark.parametrize(
    "original",
    [
        pytest.param({"a": 1}, id="original-is-an-object"),
        pytest.param(5, id="original-is-a-number"),
        pytest.param(["/x/a.jpg"], id="original-is-an-array"),
        pytest.param(True, id="original-is-a-bool"),
    ],
)
async def test_screenshots_skips_an_entry_whose_original_is_not_a_string(original: object) -> None:
    """`f"{SHIKIMORI_HOST}{path}"` interpolates anything at all — an
    object became the URL "https://shikimori.io{'a': 1}", which only
    fails later at `InputMediaPhoto(media=url)`."""
    entries = [{"original": original}, {"original": "/x/a.jpg"}]

    async with httpx.AsyncClient(transport=_responding(entries)) as client:
        urls = await shikimori.screenshots(client, 52991)

    assert urls == ["https://shikimori.io/x/a.jpg"]
