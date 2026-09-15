"""Entry-level guards shared by all four search providers.

`rest.py` closed the *container* door (issue #75): a body that isn't
JSON, or that decodes to something other than the array/object the
endpoint is documented to answer with, becomes a `RuntimeError`. This
module closes the door one level in — the container is well-formed, but
an **entry inside it** isn't shaped the way that provider's `_parse_*`
function indexes it. A result with no `id`, a `title` that arrived as a
bare string instead of an object, an array of scalars where an array of
objects belongs: every one of those raised a `KeyError`/`AttributeError`/
`TypeError` that no handler catches, so it escaped to
`app._error_handler` and left the starter on a SETUP row with a dead
keyboard and no reply (issue #83).

It lives here rather than in `rest.py` because `anilist.py` needs it
too, and `anilist.py` deliberately doesn't import `rest.py` (see that
module's docstring: GraphQL has no by-id URL and no 404 semantics, so
it shares none of the REST plumbing). Parsing is the one thing all four
providers genuinely do the same way.

**An unusable entry is skipped, not raised on.** A provider returning
four good results and one malformed one has given the starter a usable
picker; turning that into `RuntimeError` would trade four working
buttons for a "the service is down" message and a source menu, which is
both a worse outcome and a false statement about a search that plainly
ran. When *every* entry is unusable the skip yields an empty list, which
the pickers already report as "nothing found" — the degradation is
proportional to how much of the payload was actually broken. The
single-entry (by-id) path skips the same way and ends up with None,
which every caller already renders as "this pick is gone", exactly as it
renders a 404.

The container itself is the opposite case and keeps `rest.py`'s answer:
a `data`/`media`/`results` key holding a number rather than an array is
wholly unusable, there is no partial result to salvage, and iterating it
is a `TypeError` — so that raises `RuntimeError`, which
`_SEARCH_SERVICE_ERRORS` catches.

Note where the guard's boundary actually is: it wraps the *reading* of a
field, so it ends at the parse function's `return`. A field handed back
unvalidated — an `id` that was JSON `null`, an `episode_count` that
arrived as `"3"`, a `synonyms` that arrived as a bare string — is outside
it, and blows up later at whatever consumes it. That's what `require_int`,
`optional_str` and `optional_str_list` below are for, and why parse
functions validate before returning rather than trusting the try/except
around them to have covered it.

**A malformed field skips the whole entry, exactly as a malformed id
does** — it does not keep the usable half and drop the bad field. The
tempting distinction is that an entry with a good id and a bad title
could still make a working button, unlike one with no usable id at all.
It can't, usefully:

- Dropping a title leaves the field None, which is worse than it looks.
  `"?"` is only what `prioritized_title` *displays* for an all-None
  result; what gets stored is an empty candidate list, because
  `match_candidates` filters unset fields out. So the game isn't merely
  badly labelled, it is unwinnable by construction — no guess can match
  an empty list — and it looks exactly like a working game until a whole
  round has been wasted on it. The claim here is not "we never produce a
  `?` game": a well-typed entry whose titles happen to all be null does
  that today by a legitimate path, which is its own issue (#89). It's
  that we don't *newly create* one out of a malformation we could simply
  have skipped. Skipping costs one of five buttons; keeping costs a game.
- Dropping a *synonym list* is worse still, because the field is the
  answer key: a game quietly missing the answers a player would actually
  type is the same class of silent game-rule corruption the bare-string
  `synonyms` bug is, just reached from the other side.
- Keeping-but-dropping needs a second mechanism — a per-field recovery
  path with its own defaults, running alongside the raise-and-skip every
  other malformation already uses. Two mechanisms answering the same
  question differently is precisely what `require_int` refused to grow.
- The parse function can't tell which field the picker will end up
  displaying without duplicating `prioritized_title`'s language-dependent
  priority order into every provider, so "is this entry still usable?"
  isn't even answerable here.

So the rule stays the single one issue #83 set: an entry this codebase
can't read in full is an entry it skips, whichever field made it
unreadable.

Skips are logged at WARNING per `CLAUDE.md`'s table: a third party
sending an entry we can't use is a recoverable anomaly (we recover, by
dropping it), not something broken in this codebase — ERROR would
overstate it, and DEBUG would hide a provider quietly changing its
schema on us.

**`has_answer_key` (below) closes issue #89**, the gap this docstring
used to describe as still open: a well-typed entry whose title fields
are all null/absent/empty is not malformed — every helper above
correctly lets it through — but staging it hands `match_candidates()`
(`services/game/state.py`) an empty list, which is unwinnable by
construction and looks exactly like a working game until a round is
wasted on it. That is the same bad outcome the malformed-field skip
above exists to prevent, reached from the one legitimate path instead
of a malformation, so it gets the same treatment: the parse function
returns None and `parse_entry`/`parse_entries` drop it, quietly, the
same as any other "this entry isn't wanted" decision (a specials
season, a screenshot with no path) — see `parse_entry`'s docstring.
"""

from collections.abc import Callable, Iterable
from typing import Any, TypeVar

from loguru import logger

_T = TypeVar("_T")

# Every way a third-party entry can fail to be shaped the way a
# `_parse_*` function indexes it: a missing key (`raw["id"]`), a nested
# object that arrived as a scalar (`raw["title"].get(...)`), or a
# subscript of something not subscriptable. Deliberately all three and
# not just `KeyError` — a null/scalar one level down is the same class
# of third-party malformation as a missing key at the top, and guarding
# only the top would be the half-fix pattern this milestone has already
# flagged once.
_MALFORMED_ENTRY_ERRORS = (KeyError, TypeError, AttributeError)


def parse_entry(api_name: str, entry: Any, parse: Callable[[Any], _T | None]) -> _T | None:
    """Run `parse` over one third-party entry, or None if it can't be
    parsed. See the module docstring for why unusable means skipped.

    `parse` returning None is *not* a malformation and isn't logged
    here: that's the parse function's own judgement that the entry isn't
    wanted (a specials season, a screenshot with no path), and it logs
    for itself if that's worth saying."""
    # The parse function's name, not just the provider's: four providers
    # with two or three parsers each otherwise produce indistinguishable
    # warnings, and telling a payload bug from a bug in a `_parse_*`
    # function is most of what these lines are for.
    parser = getattr(parse, "__name__", repr(parse))
    if not isinstance(entry, dict):
        logger.warning(
            "{} sent a {} where an entry object belongs, skipping it in {}: {!r}",
            api_name,
            type(entry).__name__,
            parser,
            entry,
        )
        return None
    try:
        return parse(entry)
    except _MALFORMED_ENTRY_ERRORS as exc:
        logger.warning("{} sent an entry {} can't read ({!r}): {!r}", api_name, parser, exc, entry)
        return None


def parse_entries(api_name: str, container: Any, parse: Callable[[Any], _T | None]) -> list[_T]:
    """Run `parse` over every entry in a third-party array, dropping the
    ones that can't be parsed.

    `container` is typed `Any` because it comes straight out of a
    provider payload — callers hand over `data.get("data") or []` and
    friends, and the key being present-but-a-number is exactly the case
    this checks for."""
    if not isinstance(container, list):
        msg = (
            f"{api_name} sent a {type(container).__name__} where an array of "
            f"entries belongs, expected an array"
        )
        logger.error(msg)
        raise RuntimeError(msg)
    parsed: Iterable[_T | None] = (parse_entry(api_name, entry, parse) for entry in container)
    return [result for result in parsed if result is not None]


def require_int(raw: dict, key: str) -> int:
    """`raw[key]`, but only if it really is an integer.

    The guard above ends at the parse function's `return`: it protects the
    *reading* of a field, not the value read. `raw["id"]` succeeds perfectly
    well when the value is JSON `null` or a string, and the result then
    carries `id=None` all the way to a `..._pick:None` callback button that
    cannot work — the null-shaped twin of the missing-key case, and the same
    lesson `rest.get_json` learned about `null` sailing through a guard built
    for the other shape of "absent".

    Raising rather than returning None is the point: a `TypeError` here is
    caught by `parse_entry` and becomes the same WARNING-and-skip every other
    malformation gets, instead of a second mechanism doing the same job
    differently. A missing key stays a `KeyError` for the same reason — so a
    caller whose field is genuinely optional guards with an
    `if raw.get(key) is None: return <default>` ahead of the call rather than
    asking this function for a default (see `tmdb._parse_season`, which does
    exactly that for both of its fields), keeping "absent, that's fine" and
    "present but unusable" as two visibly different answers.

    `bool` is excluded explicitly because it is an `int` subclass in Python,
    but `str(True)` is `"True"` — the same broken button by a subtler route."""
    value = raw[key]
    if not isinstance(value, int) or isinstance(value, bool):
        raise TypeError(f"{key!r} is a {type(value).__name__}, expected an integer")
    return value


def optional_str(raw: dict, key: str) -> str | None:
    """`raw[key]` as a string, None when it simply isn't there, and a
    `TypeError` when it is there but isn't one.

    The sibling of `require_int` for every field a `_parse_*` function hands
    back as `str | None` — the four providers' title variants, and the
    screenshot/still paths they interpolate into an image URL. Those all
    reached a consumer that raises: a non-string title detonates at
    `", ".join(...)` in the DM setup preview, and a non-string URL is handed
    to `InputMediaPhoto(media=...)` (issue #86).

    Unlike `require_int` this one *does* carry a default, because unlike an
    id these fields are genuinely optional at every single call site — no
    provider fills in all of them (Shikimori has no `native`, AniList no
    `russian`), so the `if raw.get(key) is None: return <default>` pre-check
    `require_int`'s docstring prescribes would be copied verbatim ahead of a
    dozen calls and say nothing at any of them. The rule behind that
    docstring note is unchanged and still the point: absent and
    present-but-unusable stay two visibly different answers. They're just
    split by which helper you call rather than by a pre-check, which is
    worth doing only when "absent is fine" is the norm for a whole class of
    field rather than a one-off — a field that's optional *here* and
    mandatory elsewhere still gets the pre-check (see `anilist._parse_result`'s
    `year`, which reads `require_int` behind one)."""
    value = raw.get(key)
    if value is None:
        return None
    if not isinstance(value, str):
        raise TypeError(f"{key!r} is a {type(value).__name__}, expected a string")
    return value


def optional_str_list(raw: dict, key: str) -> list[str]:
    """`raw[key]` as a list of strings, `[]` when it isn't there, and a
    `TypeError` when it is there but isn't one.

    The synonyms field, in other words — and the one member of this family
    whose defect is silent. `{"synonyms": 5}` at least announces itself, at
    `*(game.synonyms or [])` in `services/game/state.py::match_candidates`,
    as a `TypeError` outside anyone's `except`. `{"synonyms": "Frieren"}`
    raises nothing at all: a string is iterable, so it expands into the
    per-character candidates `['F','r','i','e','r','e','n']` and single
    letters quietly become winning guesses. Nothing in the pipeline objects
    — it stores fine in the JSON column, it renders fine in the preview —
    until a player types "e" and wins. That is why the check is
    `isinstance(value, list)` rather than "is it iterable": every wrong
    shape here is wrong, and the iterable one is the dangerous one.

    Members are checked too, not just the container. A list holding one
    integer survives storage exactly as happily and detonates one step
    later, at the same `", ".join(...)` a non-string title does."""
    value = raw.get(key)
    if value is None:
        return []
    if not isinstance(value, list):
        raise TypeError(f"{key!r} is a {type(value).__name__}, expected an array of strings")
    for item in value:
        if not isinstance(item, str):
            raise TypeError(f"{key!r} holds a {type(item).__name__}, expected an array of strings")
    return value


def has_answer_key(titles: Iterable[str | None], synonyms: list[str]) -> bool:
    """Whether an entry has anything a `/guess` could ever match: at
    least one non-empty title variant, or at least one synonym. See the
    module docstring's note on issue #89 for why this check exists and
    why it belongs here rather than in `require_int`/`optional_str`/
    `optional_str_list` above — nothing about a well-typed, entirely
    empty entry is malformed, so those helpers correctly let it through;
    this is a judgement about *usability*, made once every field is
    already known-good.

    Deliberately `prioritized_title`-independent (`services/game/state.py`):
    a parse function has no `lang` to call it with, and which single
    variant the picker would end up *displaying* is beside the point —
    the question here is whether *any* variant, in any language, has
    content a guess could hit, not which one a button would show.

    **Design decision, recorded here because it has to be consistent
    across all four providers:** an entry with no title in any variant
    but *nonempty* synonyms passes. The module docstring's "?" framing
    is about the picker's button label, but the actual defect issue #89
    closes is an empty `match_candidates()` list — and synonyms populate
    that list directly (see `match_candidates`), title or no title. A
    game staged from such an entry shows a "?" button and a "?" title
    right up until a correct guess, but a correct guess is still
    possible, which is the one property this check exists to guarantee.
    Skipping it anyway would discard a genuinely winnable pick for a
    display nicety — the same over-correction issue #83 already rejected
    on the malformed-entry side of this same file. TMDB never benefits
    from this half of the rule (it has no synonyms field at all, see
    tmdb.py's module docstring), so for TMDB this check is equivalent to
    "skip when both titles are empty" — not a special case, just what the
    general rule reduces to when `synonyms` is always `[]`."""
    return any(titles) or bool(synonyms)
