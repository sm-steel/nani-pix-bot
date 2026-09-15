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

Skips are logged at WARNING per `CLAUDE.md`'s table: a third party
sending an entry we can't use is a recoverable anomaly (we recover, by
dropping it), not something broken in this codebase — ERROR would
overstate it, and DEBUG would hide a provider quietly changing its
schema on us.
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
    if not isinstance(entry, dict):
        logger.warning(
            "{} sent a {} where an entry object belongs, skipping it: {!r}",
            api_name,
            type(entry).__name__,
            entry,
        )
        return None
    try:
        return parse(entry)
    except _MALFORMED_ENTRY_ERRORS as exc:
        logger.warning("{} sent an entry this parser can't read ({!r}): {!r}", api_name, exc, entry)
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
