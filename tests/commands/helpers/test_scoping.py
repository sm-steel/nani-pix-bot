from datetime import UTC, datetime

from telegram import Chat, Message, Update

from nani_pix_bot.commands.helpers.scoping import is_game_topic, is_private_chat


def _update(*, chat_id: int, chat_type: str, message_thread_id: int | None = None) -> Update:
    chat = Chat(id=chat_id, type=chat_type)
    message = Message(
        message_id=1, date=datetime.now(UTC), chat=chat, message_thread_id=message_thread_id
    )
    return Update(update_id=1, message=message)


def test_is_private_chat_true_for_a_dm() -> None:
    update = _update(chat_id=1, chat_type="private")

    assert is_private_chat(update) is True


def test_is_private_chat_false_for_a_group() -> None:
    update = _update(chat_id=1, chat_type="supergroup")

    assert is_private_chat(update) is False


def test_is_game_topic_true_when_chat_and_thread_match() -> None:
    update = _update(chat_id=42, chat_type="supergroup", message_thread_id=7)

    assert is_game_topic(update, group_chat_id=42, game_topic_id=7) is True


def test_is_game_topic_false_for_a_different_topic() -> None:
    update = _update(chat_id=42, chat_type="supergroup", message_thread_id=8)

    assert is_game_topic(update, group_chat_id=42, game_topic_id=7) is False


def test_is_game_topic_false_for_a_different_chat() -> None:
    update = _update(chat_id=99, chat_type="supergroup", message_thread_id=7)

    assert is_game_topic(update, group_chat_id=42, game_topic_id=7) is False
