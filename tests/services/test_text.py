from nani_pix_bot.services.text import cut_at_line


def test_text_within_the_limit_is_left_alone() -> None:
    assert cut_at_line("abc\ndef", 7, "…") == "abc\ndef"


def test_a_cut_ends_at_the_last_line_break_and_fits_with_the_marker() -> None:
    cut = cut_at_line("one\ntwo\nthree", 10, "\n…")

    assert cut == "one\ntwo\n…"
    assert len(cut) <= 10


def test_a_single_long_line_is_cut_mid_line() -> None:
    assert cut_at_line("x" * 20, 10, "…") == "x" * 9 + "…"
