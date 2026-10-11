"""/seasonpass — the player's Season Pass progress and Buy Premium, in a DM (seasons spec §2)."""

from types import MappingProxyType

from loguru import logger
from sqlalchemy.orm import Session
from telegram import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Update, User
from telegram.ext import ContextTypes

from nani_pix_bot.commands.achievements.common import MAX_ID, outsider_refusal
from nani_pix_bot.commands.helpers.scoping import is_private_chat
from nani_pix_bot.db import session_scope
from nani_pix_bot.models.enums import SeasonStatus
from nani_pix_bot.models.season import SeasonPass, SeasonSchedule
from nani_pix_bot.seasons import registry
from nani_pix_bot.seasons.definition import SeasonRun
from nani_pix_bot.services import i18n, players, settings
from nani_pix_bot.services.seasons import pass_view, schedule, season_pass

PREFIX = "spass:"


def _button(text: str, action: str, season_id: int) -> InlineKeyboardButton:
    return InlineKeyboardButton(text, callback_data=f"{PREFIX}{action}:{season_id}")


def _keyboard(
    session: Session, season: SeasonSchedule, run: SeasonRun, player_id: int, lang: str
) -> InlineKeyboardMarkup | None:
    """A Buy Premium button while Premium is for sale and the player hasn't got it."""
    progress = session.get(SeasonPass, (season.id, player_id))
    bought = progress is not None and progress.premium_transfer_id is not None
    if bought or season.status != SeasonStatus.ACTIVE or not any(t.premium for t in run.tiers):
        return None
    label = i18n.t("season_pass.buy", lang, price=run.premium_price)
    return InlineKeyboardMarkup([[_button(label, "buy", season.id)]])


def view(session: Session, player_id: int, lang: str) -> tuple[str, InlineKeyboardMarkup | None]:
    """The pass text for the open season, with its Buy Premium button when on sale."""
    season = schedule.current(session)
    run = registry.get(season.run_id) if season is not None else None
    if season is None or run is None:
        return i18n.t("season_pass.none", lang), None
    text = "\n".join(pass_view.view_lines(session, season, run, player_id, lang))
    return text, _keyboard(session, season, run, player_id, lang)


async def season_pass_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    message, user = update.message, update.effective_user
    if message is None or user is None:
        return
    with session_scope(context.bot_data["session_factory"]) as session:
        lang = settings.get_language(session)
    if not is_private_chat(update):
        logger.warning("/seasonpass outside a DM")
        await message.reply_text(i18n.t("season_pass.dm_only", lang))
        return
    refusal = await outsider_refusal(context, user.id, "/seasonpass")
    if refusal is not None:
        await message.reply_text(refusal)
        return
    with session_scope(context.bot_data["session_factory"]) as session:
        players.get_or_create_player(session, user.id, username=user.username)
        text, keyboard = view(session, user.id, lang)
    await message.reply_text(text, reply_markup=keyboard)
    logger.info("showed /seasonpass")


# --- Premium taps ---


def _season_id(arg: str) -> int | None:
    return int(arg) if arg.isdecimal() and int(arg) <= MAX_ID else None


def _open_run(session: Session, season_id: int) -> tuple[SeasonSchedule, SeasonRun] | None:
    """The open season and its run, when the tap's season id is that season."""
    season = schedule.current(session)
    run = registry.get(season.run_id) if season is not None else None
    if season is None or run is None or season.id != season_id:
        return None
    return season, run


async def _refuse(
    query: CallbackQuery, lang: str, refusal: season_pass.PremiumRefusal, **numbers: int
) -> None:
    await query.answer(
        i18n.t(f"season_pass.refused.{refusal.value}", lang, **numbers), show_alert=True
    )


async def _confirm(
    query: CallbackQuery, context: ContextTypes.DEFAULT_TYPE, season_id: int
) -> None:
    with session_scope(context.bot_data["session_factory"]) as session:
        lang = settings.get_language(session)
        open_run = _open_run(session, season_id)
        price = open_run[1].premium_price if open_run else 0
    if open_run is None:
        logger.warning("refused a Premium confirm: season {tapped} isn't open", tapped=season_id)
        await _refuse(query, lang, season_pass.PremiumRefusal.NOT_OPEN)
        return
    buttons = [
        _button(i18n.t("season_pass.confirm_yes", lang), "yes", season_id),
        _button(i18n.t("season_pass.confirm_no", lang), "no", season_id),
    ]
    logger.info("asked to confirm buying Premium", season_id=season_id, price=price)
    await query.answer()
    await query.edit_message_text(
        i18n.t("season_pass.confirm", lang, price=price),
        reply_markup=InlineKeyboardMarkup([buttons]),
    )


async def _back(query: CallbackQuery, context: ContextTypes.DEFAULT_TYPE, season_id: int) -> None:
    with session_scope(context.bot_data["session_factory"]) as session:
        text, keyboard = view(session, query.from_user.id, settings.get_language(session))
    logger.info("backed out of buying Premium", season_id=season_id)
    await query.answer()
    await query.edit_message_text(text, reply_markup=keyboard)


def _try_buy(
    context: ContextTypes.DEFAULT_TYPE, user: User, season_id: int
) -> tuple[season_pass.PremiumRefusal | None, int, int]:
    """Buys Premium; returns the refusal (if any), the price and the 💠 balance before."""
    price = balance = 0
    try:
        with session_scope(context.bot_data["session_factory"]) as session:
            open_run = _open_run(session, season_id)
            if open_run is None:
                raise season_pass.PremiumRefusedError(season_pass.PremiumRefusal.NOT_OPEN)
            season, run = open_run
            player = players.get_or_create_player(session, user.id, username=user.username)
            price, balance = run.premium_price, player.currency
            season_pass.buy_premium(session, season, run, player)
    except season_pass.PremiumRefusedError as error:
        return error.refusal, price, balance
    return None, price, balance


async def _buy(query: CallbackQuery, context: ContextTypes.DEFAULT_TYPE, season_id: int) -> None:
    refusal, price, balance = _try_buy(context, query.from_user, season_id)
    with session_scope(context.bot_data["session_factory"]) as session:
        lang = settings.get_language(session)
        text, keyboard = view(session, query.from_user.id, lang)
    if refusal is not None:
        await _refuse(query, lang, refusal, price=price, balance=balance)
        return
    await query.answer()
    await query.edit_message_text(text, reply_markup=keyboard)


_TAPS = MappingProxyType({"buy": _confirm, "yes": _buy, "no": _back})


async def season_pass_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    if query is None or query.data is None or query.from_user is None:
        return
    action, _, arg = query.data.removeprefix(PREFIX).partition(":")
    tap, season_id = _TAPS.get(action), _season_id(arg)
    if tap is None or season_id is None:
        logger.warning("ignored a malformed /seasonpass tap {data!r}", data=query.data)
        await query.answer()
        return
    refusal = await outsider_refusal(context, query.from_user.id, "/seasonpass tap")
    if refusal is not None:
        await query.answer(refusal, show_alert=True)
        return
    await tap(query, context, season_id)
