"""One action per menu message (issue #479). A picker whose tap moves currency
claims its message in the same transaction as the action, so a second tap on
the same menu (a quick double tap, or a stale one) finds it claimed and does
nothing. A rolled-back action releases the claim with it."""

from loguru import logger
from sqlalchemy.orm import Session

from nani_pix_bot.models.used_menu import UsedMenu


def claim(session: Session, chat_id: int, message_id: int) -> bool:
    """Mark the menu used; False when it already was."""
    if session.get(UsedMenu, (chat_id, message_id)) is not None:
        logger.debug(
            "menu {message_id} in chat {chat} was already used", message_id=message_id, chat=chat_id
        )
        return False
    session.add(UsedMenu(chat_id=chat_id, message_id=message_id))
    session.flush()
    return True
