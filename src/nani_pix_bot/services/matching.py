"""Guess normalization + fuzzy matching — see MECHANICS.md's "Guess
matching" section. Fully local and deterministic: everything this needs
(a game's cached title variants/synonyms) is already in the database, so
no network call ever happens per guess."""

import re
import string
from collections.abc import Sequence

from loguru import logger
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
        logger.debug("Guess {!r} normalized to empty string — no match possible", guess)
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
            "  vs {!r} (normalized {!r}): score {:.1f}", candidate, normalized_candidate, score
        )
        if score > best_score:
            best_score = score
            best_candidate = candidate
            best_normalized = normalized_candidate

    matched = best_score >= MATCH_THRESHOLD
    margin = best_score - MATCH_THRESHOLD
    logger.debug(
        "Guess {!r} (normalized {!r}) best matched {!r} (normalized {!r}): "
        "score {:.1f}, threshold {:.1f}, {} by {:.1f} -> {}",
        guess,
        normalized_guess,
        best_candidate,
        best_normalized,
        best_score,
        MATCH_THRESHOLD,
        "cleared" if matched else "short",
        abs(margin),
        "MATCH" if matched else "NO MATCH",
    )
    return matched
