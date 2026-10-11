from nani_pix_bot.services.economy import wallet
from nani_pix_bot.services.seasons import xp


def test_a_win_pays_the_winner_and_the_host_in_coins(session, tagged_game, emit_win) -> None:
    emit_win(winner=1, stage=3)
    season_id = tagged_game.season_id
    assert wallet.balance(session, 1, season_id=season_id) == 25  # demo_1 coins.win
    assert wallet.balance(session, 2, season_id=season_id) == 15  # host_solved


def test_an_unsolved_game_pays_the_host_less(session, tagged_game, emit_unsolved) -> None:
    emit_unsolved()
    assert wallet.balance(session, 2, season_id=tagged_game.season_id) == 5


def test_only_the_first_guess_in_a_game_pays(session, tagged_game, emit_guess) -> None:
    emit_guess(player=1)
    emit_guess(player=1)
    assert wallet.balance(session, 1, season_id=tagged_game.season_id) == 5


def test_a_bot_started_game_pays_no_host_coins(session, bot_tagged_game, emit_win) -> None:
    emit_win(winner=1, stage=1)
    assert wallet.balance(session, 2, season_id=bot_tagged_game.season_id) == 0


def test_no_coins_once_the_season_is_over(session, tagged_game, emit_win, end_season) -> None:
    end_season()
    emit_win(winner=1, stage=1)
    assert wallet.balance(session, 1, season_id=tagged_game.season_id) == 0


def test_an_untagged_game_pays_no_coins(session, untagged_game, emit_win) -> None:
    emit_win(winner=1, stage=1)
    assert wallet.balance(session, 1, season_id=1) == 0


def test_coins_are_logged_with_the_season(session, tagged_game, emit_win, log_records) -> None:
    emit_win(winner=1, stage=1)
    lines = [r for r in log_records if r.level == "INFO" and "season coins" in r.message]
    assert {r.extra["recipient_id"] for r in lines} == {1, 2}
    assert all(r.extra["season_id"] == tagged_game.season_id for r in lines)


def test_xp_is_unchanged(session, tagged_game, emit_win) -> None:
    emit_win(winner=1, stage=3)
    totals = {p.player_id: p.xp for p in xp.standings(session, tagged_game.season_id)}
    assert totals == {1: 30, 2: 15}
