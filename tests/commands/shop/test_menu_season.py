import pytest

from nani_pix_bot.commands.shop import menu as shop_menu


@pytest.fixture(autouse=True)
def _season_runs(fake_runs):
    """The shared fake runs, for this module only."""


def test_the_menu_names_the_season_discount(session, tagged_game, buyer) -> None:
    text, _ = shop_menu.render_shop(session, tagged_game, buyer, "en")
    assert "Demo Season" in text
    assert "25%" in text


def test_the_menu_has_no_season_line_for_a_plain_game(session, untagged_game, buyer) -> None:
    text, _ = shop_menu.render_shop(session, untagged_game, buyer, "en")
    assert "Demo Season" not in text
