from dataclasses import dataclass

import pytest

from nani_pix_bot.services.search import parsing


@dataclass(frozen=True)
class _Parsed:
    identifier: int


def _parse(raw: dict) -> _Parsed:
    return _Parsed(identifier=raw["id"])


def _parse_optional(raw: dict) -> _Parsed | None:
    identifier = raw.get("id")
    return _Parsed(identifier=identifier) if identifier else None


def test_parse_entries_parses_every_well_formed_entry() -> None:
    assert parsing.parse_entries("Example", [{"id": 1}, {"id": 2}], _parse) == [
        _Parsed(identifier=1),
        _Parsed(identifier=2),
    ]


def test_parse_entries_skips_an_entry_missing_the_field_the_parser_indexes(
    records: list[tuple[str, str]],
) -> None:
    """One entry without an id doesn't make the other four unpickable."""
    assert parsing.parse_entries("Example", [{"id": 1}, {"name": "no id"}], _parse) == [
        _Parsed(identifier=1)
    ]
    assert [level for level, _ in records] == ["WARNING"]


def test_parse_entries_skips_a_scalar_entry(records: list[tuple[str, str]]) -> None:
    """An array of scalars where an array of objects belongs — the parser
    would raise AttributeError/TypeError on the first one."""
    assert parsing.parse_entries("Example", [1, 2, {"id": 3}], _parse) == [_Parsed(identifier=3)]
    assert [level for level, _ in records] == ["WARNING", "WARNING"]


def test_parse_entries_skips_an_entry_whose_nested_object_is_a_scalar() -> None:
    def parse_nested(raw: dict) -> _Parsed:
        return _Parsed(identifier=raw["title"]["id"])

    entries = [{"title": "flat"}, {"title": {"id": 2}}]

    assert parsing.parse_entries("Example", entries, parse_nested) == [_Parsed(identifier=2)]


def test_parse_entries_skips_quietly_when_the_parser_returns_none(
    records: list[tuple[str, str]],
) -> None:
    """A None return is the parser's own judgement that the entry isn't
    wanted (a specials season, a screenshot with no path) — not a
    malformation, so it isn't warned about."""
    assert parsing.parse_entries("Example", [{"id": 0}, {"id": 1}], _parse_optional) == [
        _Parsed(identifier=1)
    ]
    assert records == []


def test_parse_entries_raises_when_the_container_is_not_an_array() -> None:
    """A wholly-unusable container is an outage, not an empty gallery —
    and `for entry in 5` is a TypeError no handler catches."""
    with pytest.raises(RuntimeError, match="expected an array"):
        parsing.parse_entries("Example", 5, _parse)


def test_parse_entries_raises_on_a_string_container() -> None:
    with pytest.raises(RuntimeError, match="expected an array"):
        parsing.parse_entries("Example", "nope", _parse)


def test_parse_entry_parses_a_well_formed_entry() -> None:
    assert parsing.parse_entry("Example", {"id": 7}, _parse) == _Parsed(identifier=7)


def test_parse_entry_returns_none_for_an_entry_missing_its_field(
    records: list[tuple[str, str]],
) -> None:
    """The single-entry path has nothing left once the one entry is
    skipped — None, which every caller already reports as "this pick is
    gone", the same as a 404."""
    assert parsing.parse_entry("Example", {"name": "no id"}, _parse) is None
    assert [level for level, _ in records] == ["WARNING"]


def test_parse_entry_returns_none_for_a_scalar_entry() -> None:
    assert parsing.parse_entry("Example", 5, _parse) is None


def test_parse_entry_names_the_provider_in_its_warning(records: list[tuple[str, str]]) -> None:
    parsing.parse_entry("Shikimori", {"name": "no id"}, _parse)

    assert "Shikimori" in records[0][1]


def test_parse_entry_names_the_failing_parser_in_its_warning(
    records: list[tuple[str, str]],
) -> None:
    """Four providers times two or three parsers each: without the parse
    function's name, a payload bug and a code bug produce indistinguishable
    warnings, and "a code bug shows up as a flood of identical warnings" stops
    being a way to tell them apart."""
    parsing.parse_entry("Shikimori", {"name": "no id"}, _parse)

    assert "_parse" in records[0][1]


def test_parse_entry_names_the_failing_parser_for_a_scalar_entry(
    records: list[tuple[str, str]],
) -> None:
    parsing.parse_entry("Shikimori", 5, _parse)

    assert "_parse" in records[0][1]


def test_require_int_returns_an_integer_field() -> None:
    assert parsing.require_int({"id": 7}, "id") == 7


def test_require_int_raises_key_error_when_the_field_is_absent() -> None:
    """Absent stays a KeyError — `parse_entry` already turns that into a
    skip, and this helper shouldn't grow a second way of saying it."""
    with pytest.raises(KeyError):
        parsing.require_int({"name": "no id"}, "id")


@pytest.mark.parametrize("value", [None, "7", 7.5, [7], {"id": 7}, True])
def test_require_int_rejects_a_non_integer_field(value: object) -> None:
    """`raw["id"]` succeeds for a JSON null and for a string, so the
    missing-key guard alone lets an unusable id through into a button that
    cannot work. A bool is an int in Python but `str(True)` is "True", which
    is the same broken button."""
    with pytest.raises(TypeError, match="id"):
        parsing.require_int({"id": value}, "id")


def test_require_int_rejections_become_a_skip(records: list[tuple[str, str]]) -> None:
    """The point of raising TypeError rather than returning None: it routes
    through the guard every other malformation already goes through."""

    def parse_id(raw: dict) -> _Parsed:
        return _Parsed(identifier=parsing.require_int(raw, "id"))

    assert parsing.parse_entries("Example", [{"id": None}, {"id": 2}], parse_id) == [
        _Parsed(identifier=2)
    ]
    assert [level for level, _ in records] == ["WARNING"]


def test_optional_str_returns_a_string_field() -> None:
    assert parsing.optional_str({"name": "Frieren"}, "name") == "Frieren"


@pytest.mark.parametrize("raw", [{}, {"name": None}])
def test_optional_str_defaults_to_none_when_the_field_is_absent_or_null(raw: dict) -> None:
    """Unlike an id, a title genuinely may not be there — every provider
    has fields the others don't — so absent keeps the dataclass's own
    `str | None` default instead of raising."""
    assert parsing.optional_str(raw, "name") is None


@pytest.mark.parametrize("value", [5, 7.5, True, ["Frieren"], {"romaji": "Frieren"}])
def test_optional_str_rejects_a_present_but_non_string_field(value: object) -> None:
    """Present-but-unusable stays an outright rejection: a non-string title
    reaches `", ".join(...)` in the setup preview as a TypeError, and a
    non-string screenshot URL is interpolated into a Telegram send."""
    with pytest.raises(TypeError, match="name"):
        parsing.optional_str({"name": value}, "name")


def test_optional_str_list_returns_a_list_of_strings() -> None:
    assert parsing.optional_str_list({"synonyms": ["a", "b"]}, "synonyms") == ["a", "b"]


@pytest.mark.parametrize("raw", [{}, {"synonyms": None}, {"synonyms": []}])
def test_optional_str_list_defaults_to_empty_when_the_field_is_absent_or_null(raw: dict) -> None:
    assert parsing.optional_str_list(raw, "synonyms") == []


def test_optional_str_list_rejects_a_bare_string() -> None:
    """The defect this helper exists for. A string is iterable, so
    `*(game.synonyms or [])` expands `"Frieren"` into the per-character
    match candidates ['F','r','i','e','r','e','n'] and single letters
    become winning guesses — no exception, no log, just a silently
    corrupted answer key (issue #86)."""
    with pytest.raises(TypeError, match="synonyms"):
        parsing.optional_str_list({"synonyms": "Frieren"}, "synonyms")


@pytest.mark.parametrize("value", [5, 7.5, True, {"0": "Frieren"}])
def test_optional_str_list_rejects_a_non_array_field(value: object) -> None:
    with pytest.raises(TypeError, match="synonyms"):
        parsing.optional_str_list({"synonyms": value}, "synonyms")


@pytest.mark.parametrize("item", [5, None, True, ["Frieren"], {"a": 1}])
def test_optional_str_list_rejects_an_array_holding_a_non_string(item: object) -> None:
    """A list of the right shape holding one wrong member is the same
    defect one level in: it survives the JSON column and detonates at
    `", ".join(...)` instead."""
    with pytest.raises(TypeError, match="synonyms"):
        parsing.optional_str_list({"synonyms": ["Frieren", item]}, "synonyms")


def test_optional_str_rejections_become_a_skip(records: list[tuple[str, str]]) -> None:
    """Same routing `require_int` gets — a raise inside the guard, not a
    second mechanism returning a sentinel."""

    def parse_name(raw: dict) -> _Parsed:
        parsing.optional_str(raw, "name")
        return _Parsed(identifier=raw["id"])

    assert parsing.parse_entries("Example", [{"id": 1, "name": 5}, {"id": 2}], parse_name) == [
        _Parsed(identifier=2)
    ]
    assert [level for level, _ in records] == ["WARNING"]


def test_optional_str_list_rejections_become_a_skip(records: list[tuple[str, str]]) -> None:
    def parse_synonyms(raw: dict) -> _Parsed:
        parsing.optional_str_list(raw, "synonyms")
        return _Parsed(identifier=raw["id"])

    entries = [{"id": 1, "synonyms": "Frieren"}, {"id": 2}]

    assert parsing.parse_entries("Example", entries, parse_synonyms) == [_Parsed(identifier=2)]
    assert [level for level, _ in records] == ["WARNING"]


def test_has_answer_key_true_when_a_title_variant_is_nonempty() -> None:
    assert parsing.has_answer_key((None, "", "Frieren"), []) is True


def test_has_answer_key_true_when_synonyms_has_a_nonempty_entry() -> None:
    assert parsing.has_answer_key((None, None, None), ["Frieren"]) is True


def test_has_answer_key_false_when_titles_and_synonyms_are_all_empty() -> None:
    assert parsing.has_answer_key((None, "", None), []) is False


def test_has_answer_key_false_when_synonyms_is_a_nonempty_list_of_only_empty_strings() -> None:
    """Regression guard for the exact gap this closed: `bool(["", ""])` is
    True (the list itself is non-empty), but `match_candidates()`
    (`services/game/state.py`) filters with `if candidate` — per-item
    truthiness — so every one of those empty strings gets dropped and the
    actual answer key ends up `[]`. `has_answer_key` has to agree with
    that per-item filtering, not just check whether the list is
    non-empty, or it reports an unwinnable entry as winnable — reopening
    issue #89 through a narrower path."""
    assert parsing.has_answer_key((None, "", None), ["", ""]) is False
