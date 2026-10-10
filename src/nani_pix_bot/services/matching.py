"""Guess normalization + fuzzy matching — see MECHANICS.md's "Guess
matching" section. Fully local and deterministic: everything this needs
(a game's cached title variants/synonyms) is already in the database, so
no network call ever happens per guess."""

import re
import string
import unicodedata
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass
from typing import TypeVar

from loguru import logger
from rapidfuzz import fuzz

MATCH_THRESHOLD = 85.0

_PUNCTUATION_RE = re.compile(f"[{re.escape(string.punctuation)}]")

# Guess matching ignores numbers (issue #341): digits, ordinals as a whole
# ("2nd", "2-й"), standalone roman numerals, and the word "season" — on
# both the guess and every title, so "GITS" and "GITS 2026" match each
# other either way round. `\d` is Unicode-aware, and NFKC has already
# turned full-width digits and "½" into plain ones by the time it runs.
# An ordinal is a number with its ending: "2nd", or a Russian "2-й"
# (1-3 Cyrillic letters, hyphen optional). Matched before punctuation
# becomes a space, so the hyphen still ties the ending to its number.
_ORDINAL = r"\d+(?:st|nd|rd|th|-?[\u0430-\u044f\u0451]{1,3})"
_ORDINAL_RE = re.compile(rf"\b{_ORDINAL}\b")
_ORDINAL_WORD_RE = re.compile(rf"\W*{_ORDINAL}\W*")
_DIGIT_RE = re.compile(r"\d+")
_ROMAN_NUMERALS = frozenset(
    (
        "i", "ii", "iii", "iv", "v", "vi", "vii", "viii", "ix", "x",
        "xi", "xii", "xiii", "xiv", "xv", "xvi", "xvii", "xviii", "xix", "xx",
    )
)  # fmt: skip
_SEASON_WORD_RE = re.compile(r"seasons?|сезон\w*")
# Apostrophes join one word ("Journey's" in any of its quote styles), so
# they're dropped; every other punctuation or symbol character (a
# multiplication sign, guillemets, a katakana middle dot) separates words.
# Right/left single quote, modifier-letter apostrophe, acute accent:
_APOSTROPHES = frozenset("'`\u2019\u2018\u02bc\u00b4")

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


def _symbols_to_spaces(text: str) -> str:
    return "".join(
        "" if char in _APOSTROPHES else " " if unicodedata.category(char)[0] in "PS" else char
        for char in text
    )


def _word_key(word: str) -> str:
    return _symbols_to_spaces(unicodedata.normalize("NFKC", word).casefold()).strip()


def is_season_word(word: str) -> bool:
    """Whether a single word is "season"/"сезон" (any case or form) —
    ignored by matching and shown openly by the text clues."""
    return bool(_SEASON_WORD_RE.fullmatch(_word_key(word)))


def is_ordinal_word(word: str) -> bool:
    """Whether a single word is an ordinal number ("2nd", "2-й") — ignored
    by matching as a whole, suffix included."""
    return bool(_ORDINAL_WORD_RE.fullmatch(unicodedata.normalize("NFKC", word).casefold()))


def normalize_for_match(text: str, *, keep_numbers: bool = False) -> str:
    """How a guess and a title are compared: NFKC + casefold, every
    punctuation/symbol character a space (apostrophes dropped), season
    words removed, and — unless `keep_numbers` — digits, ordinals and
    standalone roman numerals removed too. See MECHANICS.md's "Guess
    matching"."""
    folded = unicodedata.normalize("NFKC", text).casefold()
    if not keep_numbers:
        folded = _DIGIT_RE.sub(" ", _ORDINAL_RE.sub(" ", folded))
    folded = _symbols_to_spaces(folded)
    return " ".join(
        word
        for word in folded.split()
        if not _SEASON_WORD_RE.fullmatch(word) and (keep_numbers or word not in _ROMAN_NUMERALS)
    )


def _comparable(guess: str, candidate: str) -> tuple[str, str]:
    """Both sides normalized for matching. When either has nothing left
    without its numbers ("22/7", or a guess of just "2026"), both keep
    their numbers instead — a title made only of numbers stays guessable,
    and a bare number never matches a title that has letters."""
    normalized_guess = normalize_for_match(guess)
    normalized_candidate = normalize_for_match(candidate)
    if normalized_guess and normalized_candidate:
        return normalized_guess, normalized_candidate
    return (
        normalize_for_match(guess, keep_numbers=True),
        normalize_for_match(candidate, keep_numbers=True),
    )


def _ratio(normalized_guess: str, normalized_candidate: str) -> float:
    if not normalized_guess or not normalized_candidate:
        return 0.0
    return fuzz.ratio(normalized_guess, normalized_candidate)


def best_score(guess: str, candidates: Sequence[str | None]) -> float:
    """The best fuzz ratio `guess` reaches against any candidate (0 when the
    guess normalizes to nothing) — recorded on every wrong guess so a near
    miss can be told apart from a wild one."""
    return max((_ratio(*_comparable(guess, c)) for c in candidates if c), default=0.0)


def is_match(guess: str, candidates: Sequence[str | None]) -> bool:
    """True if `guess` fuzzy-matches any of `candidates` (a game's cached
    title variants + synonyms) above MATCH_THRESHOLD, once both sides are
    normalized by normalize_for_match."""
    if not normalize_for_match(guess, keep_numbers=True):
        logger.debug("guess {guess!r} normalized to empty string — no match possible", guess=guess)
        return False

    best_candidate: str | None = None
    best_normalized: str | None = None
    best_guess_normalized: str | None = None
    best_score = 0.0
    for candidate in candidates:
        if not candidate:
            continue
        normalized_guess, normalized_candidate = _comparable(guess, candidate)
        score = _ratio(normalized_guess, normalized_candidate)
        logger.debug(
            "{guess!r} (normalized {normalized!r}) vs {candidate!r}"
            " (normalized {candidate_normalized!r}): score {score:.1f}",
            guess=guess,
            normalized=normalized_guess,
            candidate=candidate,
            candidate_normalized=normalized_candidate,
            score=score,
        )
        if score > best_score:
            best_score = score
            best_candidate = candidate
            best_normalized = normalized_candidate
            best_guess_normalized = normalized_guess

    matched = best_score >= MATCH_THRESHOLD
    margin = best_score - MATCH_THRESHOLD
    logger.debug(
        "guess {guess!r} (normalized {normalized!r}) best matched {candidate!r}"
        " (normalized {candidate_normalized!r}): score {score:.1f}, threshold {threshold:.1f},"
        " {verdict} by {margin:.1f} -> {result}",
        guess=guess,
        normalized=best_guess_normalized,
        candidate=best_candidate,
        candidate_normalized=best_normalized,
        score=best_score,
        threshold=MATCH_THRESHOLD,
        verdict="cleared" if matched else "short",
        margin=abs(margin),
        result="MATCH" if matched else "NO MATCH",
    )
    return matched


# A guess word shorter than this never counts toward a partial match —
# keeps "of"/"no"/"to" from revealing anything (issue #250).
PARTIAL_MIN_WORD_LETTERS = 3


@dataclass(frozen=True)
class PartialMatch:
    """Which candidate a wrong guess partly matched, and which of its
    whitespace-separated words (by index) the guess contained."""

    candidate: str
    word_indices: tuple[int, ...]


def _letters(word: str) -> int:
    return sum(char.isalpha() for char in word)


def _hideable(word: str) -> bool:
    """Whether a title word can stay hidden at all: not just punctuation,
    a number, a roman numeral or "season" — all of which matching ignores."""
    return bool(normalize_for_match(word))


def _fully_named(title: str, guess_tokens: set[str]) -> bool:
    """Whether the guess contains every hideable word of `title`, short
    particles included — revealing any of it would then give the whole
    title away."""
    return all(normalize(word) in guess_tokens for word in title.split() if _hideable(word))


def partial_match(
    guess: str, candidates: Sequence[str | None], *, min_letters: int
) -> PartialMatch | None:
    """The candidate a wrong guess shares the most whole words with, by
    letter count, if that's at least `min_letters` and at least one of its
    words stays hidden — a guess containing every word would reveal the
    answer. `min_letters <= 0` turns this off. See MECHANICS.md's
    "Partial matches"."""
    if min_letters <= 0:
        return None
    all_tokens = set(normalize(guess).split())
    guess_words = {w for w in all_tokens if _letters(w) >= PARTIAL_MIN_WORD_LETTERS}
    if not guess_words:
        return None
    best: PartialMatch | None = None
    best_letters = 0
    for candidate in candidates:
        if not candidate or _fully_named(candidate, all_tokens):
            continue
        words = candidate.split()
        indices = tuple(
            i for i, word in enumerate(words) if _hideable(word) and normalize(word) in guess_words
        )
        if not indices:
            continue
        letters = sum(_letters(words[i]) for i in indices)
        if letters > best_letters:
            best, best_letters = PartialMatch(candidate, indices), letters
    if best is None or best_letters < min_letters:
        return None
    return best
