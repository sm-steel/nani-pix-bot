"""`bind_update` — starts each Telegram update's structured log context
(issue #230, see log_context.py). Registered in app.py as a `TypeHandler`
in the earliest handler group, so it runs before every other handler for
the update, including player_tracking's `remember_user`.

It *resets* rather than adds: PTB handles updates one after another in
the same task, so without a reset the previous update's game and user
would carry over into this one's lines."""

from telegram import Update
from telegram.ext import ContextTypes

from nani_pix_bot import log_context


async def bind_update(update: Update, _context: ContextTypes.DEFAULT_TYPE) -> None:
    """PTB's handler signature; the callback context isn't needed here."""
    fields: dict[str, object] = {"update_id": update.update_id}
    user = update.effective_user
    if user is not None:
        fields.update(user_id=user.id, username=user.username, user_name=user.full_name)
    chat = update.effective_chat
    if chat is not None:
        fields.update(chat_id=chat.id, chat_type=chat.type)
    message = update.effective_message
    if message is not None and message.message_thread_id is not None:
        fields["thread_id"] = message.message_thread_id
    log_context.reset(**fields)
