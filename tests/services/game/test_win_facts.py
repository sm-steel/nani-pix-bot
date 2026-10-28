"""The facts a win records for its game_won event."""

import pytest

from nani_pix_bot.models.enums import WinMethod
from nani_pix_bot.seasons.mechanics import HexKind
from nani_pix_bot.services.game import win_facts
from nani_pix_bot.services.seasons import gate
from tests.hex_fixtures import add_hex
from tests.seasons_fixtures import WINNER


@pytest.fixture(autouse=True)
def _season_runs(fake_runs):
    """The shared fake runs, applied to every test in this file."""


def test_a_tagged_wins_facts_list_its_gate_tags(session, tagged_game) -> None:
    tagged_game.anime_tags = [{"kind": "genre", "name": "Romance", "mal_id": 22}]

    facts = win_facts.win_facts(session, tagged_game, WINNER, WinMethod.GUESS)

    assert facts["gate_tags"] == ["genre:22"]
    assert facts["gate_tags"] == gate.won_tags(session, tagged_game)


def test_an_untagged_wins_facts_have_no_gate_tags(session, untagged_game) -> None:
    facts = win_facts.win_facts(session, untagged_game, WINNER, WinMethod.GUESS)

    assert facts["gate_tags"] == []


def test_win_facts_mark_a_haunted_win(session, haunted_game) -> None:
    haunted_game.haunt_twist = "tanuki_leaf"

    assert win_facts.win_facts(session, haunted_game, WINNER, WinMethod.GUESS)["haunted"] is True


def test_win_facts_mark_a_boss_game_as_haunted(session, haunted_game) -> None:
    haunted_game.boss_game = True

    assert win_facts.win_facts(session, haunted_game, WINNER, WinMethod.GUESS)["haunted"] is True


def test_win_facts_mark_a_plain_win_as_not_haunted(session, haunted_game) -> None:
    assert win_facts.win_facts(session, haunted_game, WINNER, WinMethod.GUESS)["haunted"] is False


def test_win_facts_mark_a_winner_under_a_hex(session, haunted_game) -> None:
    add_hex(session, WINNER, HexKind.KAPPA)

    assert win_facts.win_facts(session, haunted_game, WINNER, WinMethod.GUESS)["hexed"] is True


def test_win_facts_mark_an_unhexed_winner(session, haunted_game) -> None:
    assert win_facts.win_facts(session, haunted_game, WINNER, WinMethod.GUESS)["hexed"] is False
