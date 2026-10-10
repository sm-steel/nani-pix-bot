"""Pure text-clue logic: the first/last letter of a title and its masked
"shape". Letters (str.isalpha, so Cyrillic and kana/kanji count) are
what's hidden and what the letter clues pick from. Guess matching
ignores numbers (services/matching.py), so digits show as DIGIT and the
letter clues skip them, an ordinal's suffix ("2nd") and a season word
are shown as-is — unless the title has no letter at all ("22/7"), where
its digits are the only thing to guess and are hidden like letters.
Spaces separate words; other punctuation is shown as-is, since it
carries no letter information."""

import re
from collections.abc import Collection
from enum import Enum, auto

from nani_pix_bot.services.matching import is_ordinal_word, is_season_word

MASK = "_"
DIGIT = "X"
WORD_GAP = "   "

_WORD_RE = re.compile(r"\S+")


class _Kind(Enum):
    LETTER = auto()  # hidden, and what the letter clues pick from
    DIGIT = auto()  # shown as DIGIT, skipped by the letter clues
    SHOWN = auto()  # punctuation, an ordinal suffix, a season word


def _plain_kind(char: str, *, digits_count: bool) -> _Kind:
    if char.isalpha() or (digits_count and char.isnumeric()):
        return _Kind.LETTER
    return _Kind.DIGIT if char.isnumeric() else _Kind.SHOWN


def _word_kinds(word: str, *, digits_count: bool) -> list[_Kind]:
    if is_season_word(word):
        return [_Kind.SHOWN] * len(word)
    if is_ordinal_word(word) and not digits_count:
        return [_Kind.DIGIT if char.isnumeric() else _Kind.SHOWN for char in word]
    return [_plain_kind(char, digits_count=digits_count) for char in word]


def _words(title: str, *, numbers_matter: bool) -> list[tuple[int, str, list[_Kind]]]:
    """Each whitespace-separated word: its start offset, text and the kind
    of each of its characters. Digits count like letters when the game's
    creator said numbers matter, or when the title has no letter at all."""
    found = [(match.start(), match.group()) for match in _WORD_RE.finditer(title)]
    digits_count = numbers_matter or all(
        kind is not _Kind.LETTER
        for _, word in found
        for kind in _word_kinds(word, digits_count=False)
    )
    return [(start, word, _word_kinds(word, digits_count=digits_count)) for start, word in found]


def _letter_positions(title: str, *, numbers_matter: bool) -> list[int]:
    return [
        start + offset
        for start, _, kinds in _words(title, numbers_matter=numbers_matter)
        for offset, kind in enumerate(kinds)
        if kind is _Kind.LETTER
    ]


def first_char(title: str, *, numbers_matter: bool = False) -> str | None:
    positions = _letter_positions(title, numbers_matter=numbers_matter)
    return title[positions[0]] if positions else None


def last_char(title: str, *, numbers_matter: bool = False) -> str | None:
    positions = _letter_positions(title, numbers_matter=numbers_matter)
    return title[positions[-1]] if positions else None


def _render(word: str, kinds: list[_Kind], shown: Collection[int] = ()) -> str:
    """One masked word, its characters space-separated; `shown` are
    in-word offsets of letters revealed as-is."""
    return " ".join(
        char
        if kind is _Kind.SHOWN or (kind is _Kind.LETTER and offset in shown)
        else MASK
        if kind is _Kind.LETTER
        else DIGIT
        for offset, (char, kind) in enumerate(zip(word, kinds, strict=True))
    )


def title_shape(
    title: str, *, reveal_first: bool, reveal_last: bool, numbers_matter: bool = False
) -> str:
    """Each letter becomes MASK, except a revealed first/last one, and each
    ignored digit DIGIT; characters within a word are space-separated and
    words are separated by WORD_GAP, so the shape reads cleanly in a
    monospace block."""
    positions = _letter_positions(title, numbers_matter=numbers_matter)
    shown = set()
    if positions and reveal_first:
        shown.add(positions[0])
    if positions and reveal_last:
        shown.add(positions[-1])
    return WORD_GAP.join(
        _render(word, kinds, {index - start for index in shown})
        for start, word, kinds in _words(title, numbers_matter=numbers_matter)
    )


def word_lengths(title: str, *, numbers_matter: bool = False) -> list[int]:
    """How many hidden letters each word has, for words that have any."""
    counts = (
        sum(kind is _Kind.LETTER for kind in kinds)
        for _, _, kinds in _words(title, numbers_matter=numbers_matter)
    )
    return [count for count in counts if count]


def words_shape(title: str, shown: Collection[int], *, numbers_matter: bool = False) -> str:
    """The partial-match reveal (issue #250): words at the `shown` indices
    (whitespace-split) as-is, every other word masked like title_shape."""
    return WORD_GAP.join(
        word if index in shown else _render(word, kinds)
        for index, (_, word, kinds) in enumerate(_words(title, numbers_matter=numbers_matter))
    )
