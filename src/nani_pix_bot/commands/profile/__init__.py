"""/profile [@username] — a player's profile card (seasons spec §5), in DM or the
game topic, with ◀ ▶ and a player list on the card (browse.py)."""

from loguru import logger
from sqlalchemy.orm import Session
from telegram import Update, User
from telegram.error import TelegramError
from telegram.ext import ContextTypes

from nani_pix_bot.commands.achievements.common import outsider_refusal
from nani_pix_bot.commands.helpers.scoping import is_game_topic, is_private_chat
from nani_pix_bot.commands.profile import browse, card
from nani_pix_bot.db import session_scope
from nani_pix_bot.services import i18n, players, settings


def _target_id(session: Session, user: User, args: list[str]) -> int | None:
    """Whose profile: the caller's, or the @username's; None for an unknown name."""
    if not args:
        return user.id
    username = args[0].lstrip("@")
    target = players.find_player_by_username(session, username)
    if target is None:
        logger.warning(
            "asked for the /profile of unknown username {target_username!r}",
            target_username=username,
        )
        return None
    return target.telegram_user_id


async def profile_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    message = update.message
    user = update.effective_user
    if message is None or user is None:
        return
    in_topic = is_game_topic(
        update,
        group_chat_id=context.bot_data["group_chat_id"],
        game_topic_id=context.bot_data["game_topic_id"],
    )
    if not (in_topic or is_private_chat(update)):
        return
    if not in_topic:
        refusal = await outsider_refusal(context, user.id, "/profile")
        if refusal is not None:
            await message.reply_text(refusal)
            return

    args = context.args or []
    with session_scope(context.bot_data["session_factory"]) as session:
        lang = settings.get_language(session)
        # The caller is in the roster even when looking at someone else, so the
        # card's "my card" button always has somewhere to go.
        players.get_or_create_player(session, user.id, username=user.username)
        target_id = _target_id(session, user, args)
        if target_id is None:
            unknown = i18n.t("profile.unknown", lang, username=args[0].lstrip("@"))
        else:
            view, headline = card.load(session, target_id, lang)
            viewing = browse.Viewing(user.id, target_id, private=not in_topic)
            keyboard = browse.card_keyboard(browse.roster(session, context, user.id), viewing, lang)
    if target_id is None:
        await message.reply_text(unknown)
        return
    png, caption = await card.photo(context, view, headline, lang)
    try:
        await message.reply_photo(photo=png, caption=caption, reply_markup=keyboard)
    except TelegramError:
        logger.opt(exception=True).error(
            "failed to send the profile card of {target}",
            target=view.name,
            target_id=target_id,
        )
        return
    logger.info(
        "showed the /profile of {target}",
        target=view.name,
        target_id=target_id,
        seasons=view.seasons_played,
    )
