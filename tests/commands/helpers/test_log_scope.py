from unittest.mock import MagicMock

from nani_pix_bot import log_context
from nani_pix_bot.commands.helpers.log_scope import bind_update


def _update(*, user: bool = True) -> MagicMock:
    update = MagicMock()
    update.update_id = 501
    update.effective_chat.id = -100
    update.effective_chat.type = "supergroup"
    update.effective_message.message_thread_id = 7
    if user:
        update.effective_user.id = 2
        update.effective_user.username = "bob"
        update.effective_user.full_name = "Bob B"
    else:
        update.effective_user = None
    return update


async def test_bind_update_sets_who_and_where() -> None:
    await bind_update(_update(), MagicMock())

    assert log_context.current() == {
        "update_id": 501,
        "user_id": 2,
        "username": "bob",
        "user_name": "Bob B",
        "chat_id": -100,
        "chat_type": "supergroup",
        "thread_id": 7,
    }


async def test_bind_update_drops_the_previous_updates_game() -> None:
    log_context.reset(game_id=88, user_id=9)

    await bind_update(_update(user=False), MagicMock())

    context = log_context.current()
    assert "game_id" not in context
    assert "user_id" not in context
    assert context["update_id"] == 501
