"""The /sharpen command and its confirm button — pay currency to advance the
round one pixelation stage. Group game topic only; see
services/economy/sharpen.py for the rules. The button only works for whoever
asked, and only while the stage it was made for is still current."""

from dataclasses import dataclass, replace

from loguru import logger
from telegram import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Update, User
from telegram.error import TelegramError
from telegram.ext import ContextTypes

from nani_pix_bot.commands.game_flow.stage_post import (
    Announcement,
    prepare_stage_advanced_announcement,
    send_announcement,
)
from nani_pix_bot.commands.helpers.actor import describe_user
from nani_pix_bot.commands.helpers.scoping import is_game_topic
from nani_pix_bot.db import session_scope
from nani_pix_bot.models.enums import PixelStage
from nani_pix_bot.models.game import Game
from nani_pix_bot.models.player import Player
from nani_pix_bot.services import game as game_service
from nani_pix_bot.services import i18n, players, settings
from nani_pix_bot.services.clues import shop
from nani_pix_bot.services.economy import sharpen

_PREFIX = "sharpen:"


@dataclass(frozen=True)
class _Tap:
    """A parsed button press: `game_id`/`stage` are None for a cancel."""

    requester_id: int
    game_id: int | None = None
    stage: PixelStage | None = None
    price: int | None = None


def _confirm_data(game: Game, requester_id: int, price: int) -> str:
    stage = game.current_stage
    if stage is None:
        raise RuntimeError(f"game {game.id} has no current_stage to sharpen")
    return f"{_PREFIX}ok:{game.id}:{stage.value}:{requester_id}:{price}"


def _cancel_data(requester_id: int) -> str:
    return f"{_PREFIX}no:{requester_id}"


def _int(text: str) -> int | None:
    return int(text) if text.isascii() and text.isdigit() else None


def _parse_confirm(parts: list[str]) -> _Tap | None:
    game_id, requester, price = _int(parts[2]), _int(parts[4]), _int(parts[5])
    try:
        stage = PixelStage(parts[3])
    except ValueError:
        return None
    if game_id is None or requester is None or price is None:
        return None
    return _Tap(requester, game_id, stage, price)


def _parse(data: str) -> _Tap | None:
    """The press `data` encodes, or None when it isn't exactly one of the two
    shapes this module builds."""
    parts = data.split(":")
    if parts[0] == "sharpen" and len(parts) == 6 and parts[1] == "ok":
        return _parse_confirm(parts)
    if parts[0] == "sharpen" and len(parts) == 3 and parts[1] == "no":
        requester = _int(parts[2])
        return None if requester is None else _Tap(requester)
    return None


def _keyboard(game: Game, requester_id: int, price: int, lang: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton(
                    i18n.t("sharpen.confirm_button", lang, price=price),
                    callback_data=_confirm_data(game, requester_id, price),
                ),
                InlineKeyboardButton(
                    i18n.t("sharpen.cancel_button", lang),
                    callback_data=_cancel_data(requester_id),
                ),
            ]
        ]
    )


def _log_prompt(game: Game, user: User, price: int) -> None:
    stage = game.current_stage
    logger.info(
        "Game {}: {} asked to /sharpen at {} — confirm prompt shown ({} 💠)",
        game.id,
        describe_user(user),
        "?" if stage is None else game_service.stage_label(stage),
        price,
    )


async def sharpen_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
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

    markup: InlineKeyboardMarkup | None = None
    with session_scope(context.bot_data["session_factory"]) as session:
        lang = settings.get_language(session)
        player = players.get_or_create_player(session, user.id, username=user.username)
        game = game_service.active_or_setup_game(session)
        refusal = sharpen.check(game, player)
        if refusal is not None:
            logger.warning("{}'s /sharpen was refused: {}", describe_user(user), refusal.value)
            reply = i18n.t(f"sharpen.refusal.{refusal.value}", lang)
        elif game is not None:
            price = sharpen.price(session)
            _log_prompt(game, user, price)
            reply = i18n.t("sharpen.confirm_prompt", lang, price=price)
            markup = _keyboard(game, user.id, price, lang)
    if markup is None:
        await message.reply_text(reply)
    else:
        await message.reply_text(reply, reply_markup=markup)


@dataclass(frozen=True)
class _Sharpened:
    """What a committed sharpen needs for its Telegram sends."""

    lang: str
    announcement: Announcement


def _apply(context: ContextTypes.DEFAULT_TYPE, user: User, tap: _Tap) -> _Sharpened:
    """One transaction: charge, advance, and render the new stage post. Raises
    SharpenRefusedError (rolling everything back) on any refusal."""
    if tap.game_id is None or tap.stage is None or tap.price is None:
        raise RuntimeError("_apply needs a confirm press")
    with session_scope(context.bot_data["session_factory"]) as session:
        lang = settings.get_language(session)
        player = players.get_or_create_player(session, user.id, username=user.username)
        game = shop.active_game_for(session, tap.game_id)
        if game is None:
            raise sharpen.SharpenRefusedError(sharpen.SharpenRefusal.NO_GAME)
        paid = sharpen.sharpen(session, game, player, sharpen.SharpenOffer(tap.stage, tap.price))
        announcement = prepare_stage_advanced_announcement(session, context, game, lang)
        prefix = i18n.t("sharpen.done", lang, name=user.full_name, amount=paid)
        return _Sharpened(lang, replace(announcement, caption=f"{prefix}\n{announcement.caption}"))


def _refusal_alert(session_factory, user: User, refusal: sharpen.SharpenRefusal) -> str:
    """The alert text for a refused confirm; its transaction already rolled
    back, so the balance is read in a fresh scope."""
    logger.warning("{}'s sharpen confirm was refused: {}", describe_user(user), refusal.value)
    with session_scope(session_factory) as session:
        lang = settings.get_language(session)
        player = session.get(Player, user.id)
        balance = player.currency if player is not None else 0
    return i18n.t(f"sharpen.refusal.{refusal.value}", lang, balance=balance)


async def _edit(query: CallbackQuery, text: str) -> None:
    try:
        await query.edit_message_text(text)
    except TelegramError as error:
        # e.g. "message is not modified"/already gone — the sharpen itself is done.
        logger.warning("Could not edit the sharpen confirm message: {}", error)


async def _confirm(context: ContextTypes.DEFAULT_TYPE, query: CallbackQuery, tap: _Tap) -> None:
    session_factory = context.bot_data["session_factory"]
    user = query.from_user
    try:
        done = _apply(context, user, tap)
    except sharpen.SharpenRefusedError as error:
        await query.answer(_refusal_alert(session_factory, user, error.refusal), show_alert=True)
        return
    # Committed above: the charge and the new stage are durable whatever the sends do.
    await _edit(query, i18n.t("sharpen.done_short", done.lang))
    await query.answer()
    await send_announcement(context, session_factory, done.announcement)


async def sharpen_callback_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    if query is None or query.data is None:
        return
    tap = _parse(query.data)
    if tap is None:
        logger.warning("Malformed sharpen callback data: {!r}", query.data)
        await query.answer()
        return
    session_factory = context.bot_data["session_factory"]
    if query.from_user.id != tap.requester_id:
        with session_scope(session_factory) as session:
            lang = settings.get_language(session)
            requester = players.describe_player_id(session, tap.requester_id)
        logger.warning(
            "{} tapped {}'s sharpen button — not theirs", describe_user(query.from_user), requester
        )
        await query.answer(i18n.t("sharpen.not_yours", lang), show_alert=True)
        return
    if tap.game_id is None:
        logger.info("{} cancelled their /sharpen", describe_user(query.from_user))
        with session_scope(session_factory) as session:
            lang = settings.get_language(session)
        await _edit(query, i18n.t("sharpen.cancelled", lang))
        await query.answer()
        return
    await _confirm(context, query, tap)
