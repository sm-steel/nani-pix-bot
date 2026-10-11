from nani_pix_bot.seasons.definition import CoinTable


def test_coin_table_reads_as_a_flat_xp_table() -> None:
    table = CoinTable(win=25, host_solved=15, host_unsolved=5, first_guess=5).as_xp_table()
    assert table.win_by_stage == (25, 25, 25, 25, 25)
    assert (table.host_solved, table.host_unsolved, table.first_guess) == (15, 5, 5)
