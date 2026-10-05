"""/pixelconfig — admin view/tune of currency amounts (services/economy/config.py).
DM-only and admin-gated like /stageconfig. Unlike stage config it is
*not* blocked mid-game: an amount change only affects future payouts."""

from loguru import logger
from telegram import Update
from telegram.ext import ContextTypes

from nani_pix_bot.commands.helpers.actor import describe_user
from nani_pix_bot.commands.helpers.membership import is_group_admin
from nani_pix_bot.commands.helpers.scoping import is_private_chat
from nani_pix_bot.db import session_scope
from nani_pix_bot.services import i18n, settings
from nani_pix_bot.services.economy import config
from nani_pix_bot.services.economy.config import EconomyKey


def _render(lang: str, amounts: dict[EconomyKey, int]) -> str:
    rows = [
        f"{key.value} — {value} — {i18n.t(f'currency_config.desc.{key.value}', lang)}"
        for key, value in amounts.items()
    ]
    return i18n.t("currency_config.header", lang) + "\n" + "\n".join(rows)


def _parse(args: list[str]) -> tuple[str, int] | None:
    if len(args) != 2:
        return None
    try:
        value = int(args[1])
    except ValueError:
        return None
    return (args[0], value) if value >= 0 else None


def _resolve(args: list[str], lang: str) -> tuple[EconomyKey, int] | str:
    """The (key, value) to set, or the reply text explaining what's wrong."""
    parsed = _parse(args)
    if parsed is None:
        return i18n.t("currency_config.usage", lang)
    raw_key, value = parsed
    try:
        key = EconomyKey(raw_key)
    except ValueError:
        return i18n.t("currency_config.unknown_key", lang, name=raw_key)
    if key in config.PRICE_KEYS and value < 1:
        return i18n.t("currency_config.price_min", lang)
    return key, value


async def currency_config_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    message = update.message
    user = update.effective_user
    if not is_private_chat(update) or message is None or user is None:
        return

    session_factory = context.bot_data["session_factory"]
    with session_scope(session_factory) as session:
        lang = settings.get_language(session)
    if not await is_group_admin(context.bot, context.bot_data["group_chat_id"], user.id):
        logger.warning("Non-admin {} tried /pixelconfig", describe_user(user))
        await message.reply_text(i18n.t("commands.admins_only", lang))
        return

    args = context.args or []
    if not args:
        with session_scope(session_factory) as session:
            amounts = config.get_amounts(session)
        logger.info("Admin {} viewed /pixelconfig", describe_user(user))
        await message.reply_text(_render(lang, amounts))
        return

    resolved = _resolve(args, lang)
    if isinstance(resolved, str):
        logger.info(
            "Admin {} sent invalid /pixelconfig args {!r} — replied with the error",
            describe_user(user),
            args,
        )
        await message.reply_text(resolved)
        return
    key, value = resolved
    with session_scope(session_factory) as session:
        config.set_amount(session, key, value)
    logger.info("Admin {} set currency config {} = {}", describe_user(user), key.value, value)
    await message.reply_text(i18n.t("currency_config.updated", lang, name=key.value, value=value))
