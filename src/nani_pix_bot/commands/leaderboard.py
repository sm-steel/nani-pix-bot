"""The /leaderboard command — see MECHANICS.md's "Leaderboard" section."""

from loguru import logger
from telegram import Update
from telegram.ext import ContextTypes

from nani_pix_bot.commands.helpers.scoping import is_game_topic
from nani_pix_bot.db import session_scope
from nani_pix_bot.models.player import Player
from nani_pix_bot.services import i18n, players, settings
from nani_pix_bot.services.achievements import titles

LEADERBOARD_SIZE = 10


def _row(rank: int, player: Player, lang: str) -> str:
    name = player.username or player.telegram_user_id
    title = titles.text(player.title_key, lang)
    suffix = f" «{title}»" if title else ""
    return f"{rank}. {name}{suffix} — {player.wins} 👑 · {player.currency} 💠"


async def leaderboard_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    message = update.message
    if message is None:
        return

    group_chat_id = context.bot_data["group_chat_id"]
    game_topic_id = context.bot_data["game_topic_id"]
    if not is_game_topic(update, group_chat_id=group_chat_id, game_topic_id=game_topic_id):
        return

    session_factory = context.bot_data["session_factory"]
    with session_scope(session_factory) as session:
        lang = settings.get_language(session)
        top = players.top_players(session, limit=LEADERBOARD_SIZE)
        lines = [_row(i, player, lang) for i, player in enumerate(top, start=1)]

    logger.info("requested /leaderboard: {count} entries", count=len(top))
    if not top:
        await message.reply_text(i18n.t("leaderboard.empty", lang))
        return

    await message.reply_text(i18n.t("leaderboard.header", lang) + "\n" + "\n".join(lines))
