"""The /bounty command — adds currency to the running round's pot for
whoever solves it. Group game topic only; see services/economy/bounty.py
for the pot rules."""

from loguru import logger
from telegram import Update
from telegram.ext import ContextTypes

from nani_pix_bot.commands.helpers.scoping import is_game_topic
from nani_pix_bot.db import session_scope
from nani_pix_bot.models.enums import GameStatus
from nani_pix_bot.models.player import Player
from nani_pix_bot.services import game as game_service
from nani_pix_bot.services import i18n, players, settings
from nani_pix_bot.services.economy import bounty


def _parse_amount(args: list[str] | None) -> int | None:
    """The positive integer in `/bounty <N>`, or None for anything else."""
    if not args or len(args) != 1 or not args[0].isascii() or not args[0].isdigit():
        return None
    amount = int(args[0])
    return amount if amount > 0 else None


async def bounty_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    message = update.message
    user = update.effective_user
    if (
        message is None
        or user is None
        or not is_game_topic(
            update,
            group_chat_id=context.bot_data["group_chat_id"],
            game_topic_id=context.bot_data["game_topic_id"],
        )
    ):
        return

    session_factory = context.bot_data["session_factory"]
    amount = _parse_amount(context.args)
    refusal: bounty.BountyRefusal | None = None
    try:
        with session_scope(session_factory) as session:
            lang = settings.get_language(session)
            if amount is None:
                logger.warning("{} sent a malformed /bounty: {!r}", user.id, context.args)
                reply = i18n.t("bounty.usage", lang, minimum=bounty.BOUNTY_MIN)
            else:
                player = players.get_or_create_player(session, user.id, username=user.username)
                game = game_service.active_or_setup_game(session)
                if game is None or game.status != GameStatus.ACTIVE:
                    logger.warning("{} tried /bounty with no active round", user.id)
                    reply = i18n.t("bounty.no_game", lang)
                else:
                    bounty.contribute(session, game, player, amount)
                    pot = bounty.pot_balance(session, game.id)
                    reply = i18n.t(
                        "bounty.added", lang, name=user.full_name, amount=amount, pot=pot
                    )
    except bounty.BountyRefusedError as error:
        refusal = error.refusal
    if refusal is not None:
        reply = _refusal_reply(session_factory, user.id, refusal)
    await message.reply_text(reply)


def _refusal_reply(session_factory, user_id: int, refusal: bounty.BountyRefusal) -> str:
    """The reply for a refused contribution; its transaction already rolled
    back, so the balance is read in a fresh scope."""
    logger.warning("{}'s /bounty was refused: {}", user_id, refusal.value)
    with session_scope(session_factory) as session:
        lang = settings.get_language(session)
        player = session.get(Player, user_id)
        balance = player.currency if player is not None else 0
    return i18n.t(
        f"bounty.refusal.{refusal.value}",
        lang,
        minimum=bounty.BOUNTY_MIN,
        balance=balance,
    )
