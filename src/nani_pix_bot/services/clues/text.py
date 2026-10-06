"""Pure text-clue logic: the first/last letter of a title and its masked
"shape". Letters and digits (str.isalnum, so Cyrillic and kana/kanji
count) are what's hidden; spaces separate words; other punctuation is
shown as-is, since it carries no letter information."""

from collections.abc import Collection

MASK = "_"
WORD_GAP = "   "


def _alnum_positions(title: str) -> list[int]:
    return [i for i, char in enumerate(title) if char.isalnum()]


def first_char(title: str) -> str | None:
    positions = _alnum_positions(title)
    return title[positions[0]] if positions else None


def last_char(title: str) -> str | None:
    positions = _alnum_positions(title)
    return title[positions[-1]] if positions else None


def title_shape(title: str, *, reveal_first: bool, reveal_last: bool) -> str:
    """Each letter/digit becomes MASK, except a revealed first/last one;
    characters within a word are space-separated and words are separated
    by WORD_GAP, so the shape reads cleanly in a monospace block."""
    positions = _alnum_positions(title)
    shown = set()
    if positions and reveal_first:
        shown.add(positions[0])
    if positions and reveal_last:
        shown.add(positions[-1])
    words: list[str] = []
    current: list[str] = []
    for index, char in enumerate(title):
        if char.isspace():
            if current:
                words.append(" ".join(current))
                current = []
            continue
        current.append(char if (not char.isalnum() or index in shown) else MASK)
    if current:
        words.append(" ".join(current))
    return WORD_GAP.join(words)


def word_lengths(title: str) -> list[int]:
    return [
        sum(char.isalnum() for char in word)
        for word in title.split()
        if any(char.isalnum() for char in word)
    ]


def words_shape(title: str, shown: Collection[int]) -> str:
    """The partial-match reveal (issue #250): words at the `shown` indices
    (whitespace-split) as-is, every other word masked like title_shape."""
    rendered = []
    for index, word in enumerate(title.split()):
        if index in shown:
            rendered.append(word)
        else:
            rendered.append(" ".join(char if not char.isalnum() else MASK for char in word))
    return WORD_GAP.join(rendered)
