from sqlalchemy.orm import Session

from nani_pix_bot.models.used_menu import UsedMenu
from nani_pix_bot.services import used_menus


def test_the_first_claim_wins_and_later_ones_lose(session: Session) -> None:
    assert used_menus.claim(session, 5, 10) is True
    assert used_menus.claim(session, 5, 10) is False
    assert session.query(UsedMenu).count() == 1


def test_menus_are_told_apart_by_chat_and_message(session: Session) -> None:
    assert used_menus.claim(session, 5, 10) is True
    assert used_menus.claim(session, 5, 11) is True
    assert used_menus.claim(session, 6, 10) is True


def test_a_rolled_back_claim_leaves_the_menu_usable(session: Session) -> None:
    used_menus.claim(session, 5, 10)
    session.rollback()

    assert used_menus.claim(session, 5, 10) is True
