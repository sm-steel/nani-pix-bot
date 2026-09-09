"""Chat/topic scoping checks shared by more than one command module. See
ARCHITECTURE.md's "Game flow, topics, and commands" section."""

from telegram import Update
from telegram.constants import ChatType


def is_private_chat(update: Update) -> bool:
    chat = update.effective_chat
    return chat is not None and chat.type == ChatType.PRIVATE


def is_game_topic(update: Update, *, group_chat_id: int, game_topic_id: int) -> bool:
    """True only for messages in the one configured game topic of the one
    configured group — commands outside it are ignored."""
    chat = update.effective_chat
    message = update.effective_message
    if chat is None or message is None:
        return False
    return chat.id == group_chat_id and message.message_thread_id == game_topic_id
