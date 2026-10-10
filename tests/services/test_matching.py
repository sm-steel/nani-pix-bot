import pytest

from nani_pix_bot.services.matching import (
    PartialMatch,
    best_score,
    is_match,
    normalize,
    normalize_for_match,
    partial_match,
    rank_by_similarity,
)

_TITLES = ["Sousou no Frieren", "Frieren: Beyond Journey's End"]
_SYNONYMS = ["Frieren", "Frieren at the Funeral"]


def test_normalize_lowercases_strips_punctuation_and_collapses_whitespace() -> None:
    assert normalize("  Frieren:  Beyond Journey's End!!  ") == "frieren beyond journeys end"


def test_is_match_accepts_the_exact_title() -> None:
    assert is_match("Sousou no Frieren", _TITLES + _SYNONYMS)


def test_is_match_accepts_a_known_synonym() -> None:
    assert is_match("frieren", _TITLES + _SYNONYMS)


def test_is_match_accepts_a_close_typo() -> None:
    assert is_match("friren", _TITLES + _SYNONYMS)


def test_is_match_rejects_unrelated_text() -> None:
    assert not is_match("attack on titan", _TITLES + _SYNONYMS)


def test_is_match_rejects_an_empty_guess() -> None:
    assert not is_match("   ", _TITLES + _SYNONYMS)


def test_is_match_ignores_blank_candidates() -> None:
    assert not is_match("frieren", ["", None, ""])  # type: ignore[list-item]


# Shikimori's own top results for "K-On" (live, issue #199), with the
# real K-On entries it ranked 17th-23rd appended after them.
_K_ON_PAGE = [
    ("One Piece: Kinkyuu Kikaku One Piece Kanzen Kouryakuhou", "Ван-Пис: Запасной план"),
    ("Kono Subarashii Sekai ni Shukufuku wo!: Kono Subarashii Choker ni Shukufuku wo!", None),
    ("Glass no Kamen desu ga the Movie: Onna Spy no Koi!", "Стеклянная маска"),
    ("Kana Kana Kazoku: Rika in Wonderland", "Семья Кана-Кана"),
    ("K-On!! Keikaku!", "Кэйон!! План!"),
    ("K-On!", "Кэйон!"),
]


def _both_titles(entry: tuple[str, str | None]) -> tuple[str, str | None]:
    return entry


def test_rank_by_similarity_lifts_a_close_title_above_unrelated_ones() -> None:
    ranked = rank_by_similarity("K-On", _K_ON_PAGE, _both_titles)

    assert ranked[0] == ("K-On!", "Кэйон!")
    assert ranked[1] == ("K-On!! Keikaku!", "Кэйон!! План!")


def test_rank_by_similarity_scores_each_item_by_its_best_title() -> None:
    page = [("Shingeki no Kyojin Season 2", "Атака титанов 2"), ("Unrelated", "Атака титанов")]

    ranked = rank_by_similarity("Атака титанов", page, _both_titles)

    assert ranked[0] == ("Unrelated", "Атака титанов")


def test_rank_by_similarity_keeps_the_original_order_for_ties() -> None:
    """Equal scores keep the provider's own order, so a query that
    already ranked well (every franchise entry scoring the same) comes
    back unchanged."""
    page = [
        ("Evangelion Movie 2: Ha", None),
        ("Shin Evangelion Movie:||", None),
        ("Shinseiki Evangelion", None),
    ]

    assert rank_by_similarity("Evangelion", page, _both_titles) == page


def test_rank_by_similarity_keeps_the_original_order_for_a_blank_query() -> None:
    assert rank_by_similarity("  !! ", _K_ON_PAGE, _both_titles) == _K_ON_PAGE


def test_rank_by_similarity_ranks_an_item_with_no_titles_last() -> None:
    page = [(None, None), ("K-On!", None)]

    ranked = rank_by_similarity("K-On", page, lambda entry: entry)

    assert ranked == [("K-On!", None), (None, None)]


def test_is_match_names_the_guess_on_every_per_candidate_line(log_records) -> None:
    """Each "vs" line says what was compared against the candidate, so it
    reads on its own when filtered or interleaved (issue #237)."""
    is_match("Mai Otome Zwei", ["Mai-Otome 0: S.ifr", "Mai-Otome Zero"])

    per_candidate = [
        line for line in log_records if " vs " in line.message and "threshold" not in line.extra
    ]
    assert len(per_candidate) == 2
    for line in per_candidate:
        assert line.message.startswith("'Mai Otome Zwei' (normalized 'mai otome zwei') vs ")
        assert line.extra["guess"] == "Mai Otome Zwei"
        assert line.extra["normalized"] == "mai otome zwei"
        assert line.extra["candidate_normalized"] in {"mai otome s ifr", "mai otome zero"}


TITLES = ["Buddy Complex: Kanketsu-hen", "Buddy Complex: Into the Skies of Tomorrow"]


def test_partial_match_picks_the_matched_words() -> None:
    assert partial_match("buddy complex", TITLES, min_letters=4) == PartialMatch(TITLES[0], (0, 1))


def test_partial_match_needs_min_letters() -> None:
    assert partial_match("the", ["The Skies of Tomorrow"], min_letters=4) is None
    assert partial_match("skies", ["The Skies of Tomorrow"], min_letters=4) == PartialMatch(
        "The Skies of Tomorrow", (1,)
    )


def test_short_guess_words_are_ignored() -> None:
    assert partial_match("of no", ["Shingeki no Kyojin of Titans"], min_letters=1) is None


def test_off_when_min_letters_is_zero() -> None:
    assert partial_match("buddy complex", TITLES, min_letters=0) is None


def test_every_word_matched_reveals_nothing() -> None:
    # A wrong guess containing the whole title must not reveal it.
    assert partial_match("complex buddy", ["Buddy Complex"], min_letters=4) is None


def test_cyrillic_and_punctuation() -> None:
    title = "Дружеский комплекс: Последняя глава"
    assert partial_match("дружеский", [title], min_letters=4) == PartialMatch(title, (0,))
    assert partial_match("complex!!", ["Buddy Complex: Final"], min_letters=4) == PartialMatch(
        "Buddy Complex: Final", (1,)
    )


def test_most_letters_wins_across_candidates() -> None:
    match = partial_match("tomorrow skies", ["Buddy Complex", *TITLES], min_letters=4)
    assert match == PartialMatch(TITLES[1], (4, 6))


def test_letter_free_tokens_do_not_count_as_hidden_words() -> None:
    # All real words guessed (but not fuzzy-matching): the lone "-" must not
    # count as a word that stays hidden.
    title = "Kono Subarashii - Sekai"
    assert partial_match("sekai kono subarashii", [title], min_letters=4) is None


def test_short_particles_in_the_guess_still_cover_their_title_words() -> None:
    # "on"/"no" are too short to be revealed, but a guess containing them
    # plus every other word still names the whole title.
    assert partial_match("titan on attack", ["Attack on Titan"], min_letters=4) is None
    assert partial_match("no kyojin shingeki", ["Shingeki no Kyojin"], min_letters=4) is None


def test_best_score_is_the_highest_ratio_against_any_candidate() -> None:
    assert best_score("frieren", ["Naruto", "Frieren"]) == 100.0
    assert 0 < best_score("frieran", ["Frieren"]) < 100.0


def test_best_score_is_zero_for_an_empty_guess_or_no_candidates() -> None:
    assert best_score("   ", ["Frieren"]) == 0.0
    assert best_score("frieren", [None, ""]) == 0.0


# --- Numbers, ordinals, roman numerals, season words, symbols (issues #341, #342)

# Built with chr() so the look-alike characters stay explicit in the source.
_TIMES = chr(0xD7)  # multiplication sign, as in "Spy x Family"
_RIGHT_QUOTE = chr(0x2019)  # typographic apostrophe


def _fullwidth(text: str) -> str:
    return "".join(chr(0x3000) if c == " " else chr(ord(c) + 0xFEE0) for c in text)


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("GITS 2026", "gits"),
        (_fullwidth("GITS 2026"), "gits"),
        ("Mob Psycho 100 III", "mob psycho"),
        ("Re:Zero 2nd Season", "re zero"),
        ("Ghost in the Shell: S.A.C. 2nd GIG", "ghost in the shell s a c gig"),
        ("«Атака титанов» 2-й сезон", "атака титанов"),
        ("Атака титанов: Сезона 3", "атака титанов"),
        (f"Spy{_TIMES}Family", "spy family"),
        (f"Frieren: Beyond Journey{_RIGHT_QUOTE}s End", "frieren beyond journeys end"),
        ("Gintama°", "gintama"),
        ("Hunter x Hunter (2011)", "hunter hunter"),
        ("5-toubun no Hanayome", "toubun no hanayome"),
        ("Ranma ½", "ranma"),
        ("86 -エイティシックス-", "エイティシックス"),
        ("22/7", ""),
    ],
)
def test_normalize_for_match_drops_numbers_season_words_and_symbols(text, expected) -> None:
    assert normalize_for_match(text) == expected


def test_normalize_for_match_can_keep_numbers() -> None:
    assert normalize_for_match("22/7", keep_numbers=True) == "22 7"
    assert normalize_for_match("Overlord II Season 2", keep_numbers=True) == "overlord ii 2"


@pytest.mark.parametrize(
    ("guess", "title"),
    [
        ("GITS", "GITS 2026"),
        ("GITS 2026", "GITS"),
        ("gits 2025", "GITS 2026"),
        ("mob psycho", "Mob Psycho 100"),
        ("mob psycho 100", "Mob Psycho 100 III"),
        ("steins gate", "Steins;Gate 0"),
        ("steinsgate", "Steins;Gate 0"),
        ("ghost in the shell sac 2nd gig", "Ghost in the Shell: S.A.C. 2nd GIG"),
        ("re zero", "Re:Zero 2nd Season"),
        ("hunter x hunter", "Hunter x Hunter (2011)"),
        ("gundam", "Gundam 00"),
        ("overlord", "Overlord II"),
        ("code geass r", "Code Geass R2"),
        ("spy family", f"Spy{_TIMES}Family"),
        ("spyfamily", f"Spy{_TIMES}Family"),
        ("frieren beyond journeys end", f"Frieren: Beyond Journey{_RIGHT_QUOTE}s End"),
        ("shingeki no kyojin", "Shingeki no Kyojin Season 2"),
        ("атака титанов", "Атака титанов 2 сезон"),
        ("атака титанов", "«Атака титанов» 2-й сезон"),
        ("gits", _fullwidth("GITS 2026")),
        ("22/7", "22/7"),
        ("22 7", "22/7"),
        ("227", "22/7"),
        ("86", "86"),
    ],
)
def test_is_match_ignores_numbers(guess, title) -> None:
    assert is_match(guess, [title])


@pytest.mark.parametrize(
    ("guess", "title"),
    [
        ("2026", "GITS 2026"),
        ("season", "Shingeki no Kyojin Season 2"),
        ("22/8", "22/7"),
        ("gits", "22/7"),
        ("mob psycho", "Mob Talker 100"),
    ],
)
def test_is_match_still_rejects_wrong_guesses_around_numbers(guess, title) -> None:
    assert not is_match(guess, [title])


def test_best_score_ignores_numbers_too() -> None:
    assert best_score("GITS", ["GITS 2026"]) == 100.0
    assert best_score("2026", ["GITS 2026"]) < 85.0


def test_partial_match_never_counts_number_or_season_words() -> None:
    # "100" is not a word to reveal; "psycho" alone is 6 letters.
    assert partial_match("psycho 100", ["Mob Psycho 100"], min_letters=4) == PartialMatch(
        "Mob Psycho 100", (1,)
    )
    assert partial_match("season 2026", ["Shingeki no Kyojin Season 2"], min_letters=1) is None


def test_partial_match_numbers_never_count_as_hidden_words() -> None:
    # Every letter-bearing word guessed names the title, even with "2026" left out.
    assert partial_match("ghost stories", ["Ghost Stories 2026"], min_letters=4) is None
