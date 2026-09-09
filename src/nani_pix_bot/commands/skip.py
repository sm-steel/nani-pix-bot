"""The /skip command — hands off or opens the turn to start the next
game. See MECHANICS.md's "Turn handoff" section."""

from telegram import Update
from telegram.ext import ContextTypes

from nani_pix_bot.commands.helpers.scoping import is_game_topic
from nani_pix_bot.db import session_scope
from nani_pix_bot.services import game as game_service


async def skip_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    message = update.message
    user = update.effective_user
    if message is None or user is None:
        return

    group_chat_id = context.bot_data["group_chat_id"]
    game_topic_id = context.bot_data["game_topic_id"]
    if not is_game_topic(update, group_chat_id=group_chat_id, game_topic_id=game_topic_id):
        return

    session_factory = context.bot_data["session_factory"]
    with session_scope(session_factory) as session:
        if game_service.active_or_setup_game(session) is not None:
            await message.reply_text("Can't skip while a game is running.")
            return

        turn_state = game_service.get_turn_state(session)
        current = turn_state.next_starter_id if turn_state is not None else None
        if current is not None and current != user.id:
            await message.reply_text("It's not your turn to skip.")
            return

        if not context.args:
            game_service.set_next_starter(session, None)
            await message.reply_text("Turn opened up — anyone can start the next game!")
            return

        target_username = context.args[0].lstrip("@")
        target = game_service.find_player_by_username(session, target_username)
        if target is None:
            await message.reply_text(
                f"I don't know anyone called @{target_username} yet — "
                "they need to message me at least once first."
            )
            return

        game_service.set_next_starter(session, target.telegram_user_id)
        await message.reply_text(f"Turn passed to @{target_username}.")
