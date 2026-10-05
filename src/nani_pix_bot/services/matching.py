"""Guess normalization + fuzzy matching — see MECHANICS.md's "Guess
matching" section. Fully local and deterministic: everything this needs
(a game's cached title variants/synonyms) is already in the database, so
no network call ever happens per guess."""

import re
import string
from collections.abc import Callable, Iterable, Sequence
from typing import TypeVar

from loguru import logger
from rapidfuzz import fuzz

MATCH_THRESHOLD = 85.0

_PUNCTUATION_RE = re.compile(f"[{re.escape(string.punctuation)}]")

_T = TypeVar("_T")


def _search_score(normalized_query: str, titles: Iterable[str | None]) -> float:
    """Best `WRatio` between the query and any of an item's titles —
    see rank_by_similarity."""
    return max(
        (fuzz.WRatio(normalized_query, normalize(title)) for title in titles if title),
        default=0.0,
    )


def rank_by_similarity(
    query: str, items: Sequence[_T], titles: Callable[[_T], Iterable[str | None]]
) -> list[_T]:
    """`items` reordered best-first by how closely any of their
    `titles` resemble `query`, both sides normalized. Used to re-rank a
    provider's own search page whose ordering is poor (issue #199:
    Shikimori ranks "K-On!" 17th+ for the query "K-On").

    `WRatio` rather than `is_match`'s plain `ratio`: on live Shikimori
    pages, `ratio` punished long franchise titles and pulled unrelated
    short ones ("Divine Gate" for "Steins Gate") into the top, while
    `token_set_ratio` scored every entry containing the query a flat
    100. `WRatio` lifts the near-exact match and otherwise gives a
    franchise's entries equal scores — and the sort is stable, so equal
    scores (or a query that normalizes to nothing) keep the provider's
    own order, leaving a page it already ranked well unchanged."""
    normalized_query = normalize(query)
    if not normalized_query:
        return list(items)
    return sorted(items, key=lambda item: -_search_score(normalized_query, titles(item)))


def normalize(text: str) -> str:
    """Lowercase, strip punctuation, and collapse whitespace."""
    stripped = _PUNCTUATION_RE.sub("", text.lower())
    return " ".join(stripped.split())


def is_match(guess: str, candidates: Sequence[str | None]) -> bool:
    """True if `guess` fuzzy-matches any of `candidates` (a game's cached
    title variants + synonyms) above MATCH_THRESHOLD, once both sides are
    normalized."""
    normalized_guess = normalize(guess)
    if not normalized_guess:
        logger.debug("guess {guess!r} normalized to empty string — no match possible", guess=guess)
        return False

    best_candidate: str | None = None
    best_normalized: str | None = None
    best_score = 0.0
    for candidate in candidates:
        if not candidate:
            continue
        normalized_candidate = normalize(candidate)
        score = fuzz.ratio(normalized_guess, normalized_candidate)
        logger.debug(
            "  vs {candidate!r} (normalized {normalized!r}): score {score:.1f}",
            candidate=candidate,
            normalized=normalized_candidate,
            score=score,
        )
        if score > best_score:
            best_score = score
            best_candidate = candidate
            best_normalized = normalized_candidate

    matched = best_score >= MATCH_THRESHOLD
    margin = best_score - MATCH_THRESHOLD
    logger.debug(
        "guess {guess!r} (normalized {normalized!r}) best matched {candidate!r}"
        " (normalized {candidate_normalized!r}): score {score:.1f}, threshold {threshold:.1f},"
        " {verdict} by {margin:.1f} -> {result}",
        guess=guess,
        normalized=normalized_guess,
        candidate=best_candidate,
        candidate_normalized=best_normalized,
        score=best_score,
        threshold=MATCH_THRESHOLD,
        verdict="cleared" if matched else "short",
        margin=abs(margin),
        result="MATCH" if matched else "NO MATCH",
    )
    return matched
