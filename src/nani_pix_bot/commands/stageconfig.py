"""Admin commands for viewing/tuning per-stage pixelation config — see
services/stage_config.py. All three commands are DM-only, admin-gated
(same precedent as /language), and apply changes immediately with no
confirmation step (this is a fast-iteration admin tool, not a
player-facing flow — plain English, no i18n investment, same pragmatic
call as commands/testpixels.py).

Editing is blocked while a game is SETUP or ACTIVE — retuning mid-round
would pull the rug out from under whoever's playing — with a "stop the
game" button offered right there, reusing /stop's own confirm keyboard
and handler (no new callback wiring needed)."""

from pathlib import Path

from loguru import logger
from telegram import InputMediaPhoto, Message, Update
from telegram.ext import ContextTypes

from nani_pix_bot.commands.helpers.keyboards import stop_confirm_keyboard
from nani_pix_bot.commands.helpers.membership import is_group_admin
from nani_pix_bot.commands.helpers.scoping import is_private_chat
from nani_pix_bot.db import session_scope
from nani_pix_bot.models.enums import PixelStage
from nani_pix_bot.services import game as game_service
from nani_pix_bot.services import settings, stage_config
from nani_pix_bot.services.pixelate import pixelate

_EXAMPLE_IMAGES = [Path("tmp/example1.jpg"), Path("tmp/example2.png")]

_SETSTAGECONFIG_USAGE = (
    "Usage: /setstageconfig <width:guesses> x5, one per stage in order "
    "(stage 1 to stage 5), e.g. /setstageconfig 64:1 80:1 128:2 192:3 512:3"
)
_SETSTAGE_USAGE = "Usage: /setstage <stage 1-5> <width> <guesses>"


async def _is_admin(context: ContextTypes.DEFAULT_TYPE, user_id: int) -> bool:
    group_chat_id = context.bot_data["group_chat_id"]
    return await is_group_admin(context.bot, group_chat_id, user_id)


def _render_table(config: dict[PixelStage, stage_config.StageSettings]) -> str:
    header = f"{'Stage':<8}{'Width':<8}Guesses"
    rows = [header]
    for stage in PixelStage:
        settings_ = config[stage]
        rows.append(f"{stage.name:<8}{settings_.target_width:<8}{settings_.wrong_guess_limit}")
    return "<pre>" + "\n".join(rows) + "</pre>"


def _parse_pair(arg: str) -> tuple[int, int] | None:
    parts = arg.split(":")
    if len(parts) != 2:
        return None
    try:
        width, limit = int(parts[0]), int(parts[1])
    except ValueError:
        return None
    if width <= 0 or limit <= 0:
        return None
    return width, limit


async def stageconfig_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    message = update.message
    user = update.effective_user
    if not is_private_chat(update) or message is None or user is None:
        return
    if not await _is_admin(context, user.id):
        logger.warning("Non-admin {} tried /stageconfig", user.id)
        await message.reply_text("Admins only.")
        return

    session_factory = context.bot_data["session_factory"]
    with session_scope(session_factory) as session:
        config = stage_config.get_stage_config(session)
    await message.reply_text(_render_table(config), parse_mode="HTML")


async def setstageconfig_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    message = update.message
    user = update.effective_user
    if not is_private_chat(update) or message is None or user is None:
        return
    if not await _is_admin(context, user.id):
        logger.warning("Non-admin {} tried /setstageconfig", user.id)
        await message.reply_text("Admins only.")
        return

    args = context.args or []
    if len(args) != len(PixelStage):
        await message.reply_text(_SETSTAGECONFIG_USAGE)
        return

    changes: dict[PixelStage, tuple[int, int]] = {}
    for stage, arg in zip(PixelStage, args, strict=True):
        parsed = _parse_pair(arg)
        if parsed is None:
            await message.reply_text(_SETSTAGECONFIG_USAGE)
            return
        changes[stage] = parsed

    await _apply_stage_changes(message, context, changes)


async def setstage_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    message = update.message
    user = update.effective_user
    if not is_private_chat(update) or message is None or user is None:
        return
    if not await _is_admin(context, user.id):
        logger.warning("Non-admin {} tried /setstage", user.id)
        await message.reply_text("Admins only.")
        return

    args = context.args or []
    if len(args) != 3:
        await message.reply_text(_SETSTAGE_USAGE)
        return
    try:
        stage_number, width, limit = int(args[0]), int(args[1]), int(args[2])
    except ValueError:
        await message.reply_text(_SETSTAGE_USAGE)
        return
    if not (1 <= stage_number <= len(game_service.STAGE_ORDER)) or width <= 0 or limit <= 0:
        await message.reply_text(_SETSTAGE_USAGE)
        return

    stage = game_service.STAGE_ORDER[stage_number - 1]
    await _apply_stage_changes(message, context, {stage: (width, limit)})


async def _apply_stage_changes(
    message: Message,
    context: ContextTypes.DEFAULT_TYPE,
    changes: dict[PixelStage, tuple[int, int]],
) -> None:
    """Shared tail end of both setters: blocks while a game is running
    (offering to stop it), otherwise applies every change, replies with
    the updated table, then sends a visual preview per changed stage."""
    session_factory = context.bot_data["session_factory"]
    with session_scope(session_factory) as session:
        running = game_service.active_or_setup_game(session)
        if running is not None:
            lang = settings.get_language(session)
            logger.warning("Stage config edit blocked — game {} is {}", running.id, running.status)
            await message.reply_text(
                "A game is currently running — stop it before editing stage config.",
                reply_markup=stop_confirm_keyboard(lang),
            )
            return

        for stage, (width, limit) in changes.items():
            stage_config.set_stage_config(
                session, stage, target_width=width, wrong_guess_limit=limit
            )
        config = stage_config.get_stage_config(session)

    await message.reply_text(_render_table(config), parse_mode="HTML")
    await _send_preview(message, context, changes)


async def _send_preview(
    message: Message,
    context: ContextTypes.DEFAULT_TYPE,
    changes: dict[PixelStage, tuple[int, int]],
) -> None:
    chat_id = message.chat_id
    thread_id = message.message_thread_id
    for stage in sorted(changes, key=lambda s: s.name):
        width, limit = changes[stage]
        media = []
        for index, image_path in enumerate(_EXAMPLE_IMAGES):
            if not image_path.exists():
                logger.warning("stageconfig preview: missing example image {}", image_path)
                continue
            pixelated = pixelate(image_path.read_bytes(), width)
            caption = (
                f"{stage.name} — {width}px, {limit} wrong guesses allowed" if index == 0 else None
            )
            media.append(InputMediaPhoto(media=pixelated, caption=caption))
        if media:
            await context.bot.send_media_group(
                chat_id=chat_id, message_thread_id=thread_id, media=media
            )
