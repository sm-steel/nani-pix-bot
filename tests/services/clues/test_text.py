import pytest

from nani_pix_bot.services.clues import text


@pytest.mark.parametrize(
    ("title", "first", "last"),
    [
        ("Frieren: Beyond Journey's End", "F", "d"),
        ("86: Eighty-Six", "8", "x"),
        ("«Провожающая в последний путь Фрирен»", "П", "н"),
        ("葬送のフリーレン", "葬", "ン"),
        ("...", None, None),
    ],
)
def test_first_and_last_char_skip_punctuation(title, first, last) -> None:
    assert text.first_char(title) == first
    assert text.last_char(title) == last


def test_title_shape_masks_letters_and_keeps_punctuation() -> None:
    assert (
        text.title_shape("Steins;Gate 0", reveal_first=False, reveal_last=False)
        == "_ _ _ _ _ _ ; _ _ _ _   _"
    )


def test_title_shape_fills_revealed_first_and_last() -> None:
    assert (
        text.title_shape("Steins;Gate 0", reveal_first=True, reveal_last=True)
        == "S _ _ _ _ _ ; _ _ _ _   0"
    )


def test_title_shape_handles_cyrillic_and_spaceless_titles() -> None:
    assert text.title_shape("Дом Лис", reveal_first=True, reveal_last=False) == "Д _ _   _ _ _"
    assert text.title_shape("葬送", reveal_first=False, reveal_last=True) == "_ 送"


def test_word_lengths_count_letters_and_digits_only() -> None:
    assert text.word_lengths("Steins;Gate 0") == [10, 1]
    assert text.word_lengths("86: Eighty-Six") == [2, 9]


def test_words_shape_shows_only_the_given_words() -> None:
    assert text.words_shape("Buddy Complex: Into the", {0, 1}) == (
        "Buddy   Complex:   _ _ _ _   _ _ _"
    )
    assert text.words_shape("K-On! Movie", {1}) == "_ - _ _ !   Movie"
