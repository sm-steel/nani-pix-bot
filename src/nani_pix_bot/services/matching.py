"""Guess normalization + fuzzy matching — see MECHANICS.md's "Guess
matching" section. Fully local and deterministic: everything this needs
(a game's cached title variants/synonyms) is already in the database, so
no network call ever happens per guess."""

import re
import string
from collections.abc import Sequence

from rapidfuzz import fuzz

MATCH_THRESHOLD = 85.0

_PUNCTUATION_RE = re.compile(f"[{re.escape(string.punctuation)}]")


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
        return False
    return any(
        fuzz.ratio(normalized_guess, normalize(candidate)) >= MATCH_THRESHOLD
        for candidate in candidates
        if candidate
    )
