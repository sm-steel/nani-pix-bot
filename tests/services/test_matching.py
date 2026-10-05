from nani_pix_bot.services.matching import (
    PartialMatch,
    is_match,
    normalize,
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
        assert line.extra["candidate_normalized"] in {"maiotome 0 sifr", "maiotome zero"}


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
