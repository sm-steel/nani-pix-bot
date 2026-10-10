import pytest

from nani_pix_bot.services.clues import text


@pytest.mark.parametrize(
    ("title", "first", "last"),
    [
        ("Frieren: Beyond Journey's End", "F", "d"),
        ("«Провожающая в последний путь Фрирен»", "П", "н"),
        ("葬送のフリーレン", "葬", "ン"),
        ("...", None, None),
    ],
)
def test_first_and_last_char_skip_punctuation(title, first, last) -> None:
    assert text.first_char(title) == first
    assert text.last_char(title) == last


@pytest.mark.parametrize(
    ("title", "first", "last"),
    [
        ("86: Eighty-Six", "E", "x"),
        ("GITS 2026", "G", "S"),
        ("Mob Psycho 100", "M", "o"),
        ("Re:Zero 2nd Season", "R", "o"),
        ("«Фрирен» 2-й сезон", "Ф", "н"),
        ("Season 2 of Nothing", "o", "g"),
    ],
)
def test_first_and_last_letter_skip_numbers_ordinals_and_season_words(title, first, last) -> None:
    assert text.first_char(title) == first
    assert text.last_char(title) == last


def test_a_title_made_only_of_numbers_counts_its_digits() -> None:
    assert text.first_char("22/7") == "2"
    assert text.last_char("22/7") == "7"


def test_title_shape_masks_letters_shows_digits_as_x_and_keeps_punctuation() -> None:
    assert (
        text.title_shape("Steins;Gate 0", reveal_first=False, reveal_last=False)
        == "_ _ _ _ _ _ ; _ _ _ _   X"
    )
    assert text.title_shape("GITS 2026", reveal_first=False, reveal_last=False) == (
        "_ _ _ _   X X X X"
    )


def test_title_shape_fills_revealed_first_and_last_letters_only() -> None:
    assert (
        text.title_shape("Steins;Gate 0", reveal_first=True, reveal_last=True)
        == "S _ _ _ _ _ ; _ _ _ e   X"
    )


def test_title_shape_shows_season_words_and_ordinal_suffixes_openly() -> None:
    assert text.title_shape("Re:Zero 2nd Season", reveal_first=False, reveal_last=True) == (
        "_ _ : _ _ _ o   X n d   S e a s o n"
    )
    assert text.title_shape("Атака титанов 2-й сезон", reveal_first=False, reveal_last=False) == (
        "_ _ _ _ _   _ _ _ _ _ _ _   X - й   " + " ".join("сезон")
    )


def test_title_shape_hides_the_digits_of_a_title_made_only_of_numbers() -> None:
    assert text.title_shape("22/7", reveal_first=False, reveal_last=False) == "_ _ / _"
    assert text.title_shape("22/7", reveal_first=True, reveal_last=True) == "2 _ / 7"


def test_title_shape_handles_cyrillic_and_spaceless_titles() -> None:
    assert text.title_shape("Дом Лис", reveal_first=True, reveal_last=False) == "Д _ _   _ _ _"
    assert text.title_shape("葬送", reveal_first=False, reveal_last=True) == "_ 送"


def test_word_lengths_count_letters_only() -> None:
    assert text.word_lengths("Steins;Gate 0") == [10]
    assert text.word_lengths("86: Eighty-Six") == [9]
    assert text.word_lengths("Re:Zero 2nd Season") == [6]
    assert text.word_lengths("22/7") == [3]


def test_words_shape_shows_only_the_given_words() -> None:
    assert text.words_shape("Buddy Complex: Into the", {0, 1}) == (
        "Buddy   Complex:   _ _ _ _   _ _ _"
    )
    assert text.words_shape("K-On! Movie", {1}) == "_ - _ _ !   Movie"


def test_words_shape_masks_numbers_like_title_shape() -> None:
    assert text.words_shape("Mob Psycho 100 Season 2", {1}) == (
        "_ _ _   Psycho   X X X   S e a s o n   X"
    )


def test_when_numbers_matter_digits_are_hidden_and_counted_like_letters() -> None:
    assert text.title_shape(
        "GITS 2026", reveal_first=False, reveal_last=True, numbers_matter=True
    ) == ("_ _ _ _   _ _ _ 6")
    assert text.last_char("Re:Zero 2nd Season", numbers_matter=True) == "d"
    assert text.first_char("91 Days", numbers_matter=True) == "9"
    assert text.word_lengths("91 Days", numbers_matter=True) == [2, 4]
    assert text.words_shape("Mob Psycho 100", {1}, numbers_matter=True) == (
        "_ _ _   Psycho   _ _ _"
    )
