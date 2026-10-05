"""Admin commands for viewing/tuning per-stage pixelation config — see
services/settings/stage_config.py. All three commands are DM-only and
admin-gated (same precedent as /language), and apply changes
immediately with no confirmation step (this is a fast-iteration admin
tool).

Editing is blocked while a game is SETUP or ACTIVE — retuning mid-round
would pull the rug out from under whoever's playing — with a "stop the
game" button offered right there, reusing /stop's own confirm keyboard
and handler (no new callback wiring needed)."""

from collections.abc import Callable
from pathlib import Path

from loguru import logger
from telegram import InputMediaPhoto, Message, Update
from telegram.ext import ContextTypes

from nani_pix_bot.commands.helpers.keyboards import stop_confirm_keyboard
from nani_pix_bot.commands.helpers.membership import is_group_admin
from nani_pix_bot.commands.helpers.scoping import is_private_chat
from nani_pix_bot.db import session_scope
from nani_pix_bot.models.enums import DEFAULT_ALGORITHM, PixelStage
from nani_pix_bot.services import game as game_service
from nani_pix_bot.services import i18n, settings
from nani_pix_bot.services.pixelate import pixelate
from nani_pix_bot.services.settings import stage_config

# Anchored to this module rather than the working directory, the same
# way services/i18n.py resolves locales/ — these ship inside the
# installed package, so a relative path would only work when the bot is
# run from the repo root.
_ASSETS_DIR = Path(__file__).resolve().parent.parent / "assets"
_EXAMPLE_IMAGES = [_ASSETS_DIR / "example1.jpg", _ASSETS_DIR / "example2.png"]
_USAGE_KEYS = {
    "setstage": "stageconfig.setstage_usage",
    "setstageconfig": "stageconfig.setstageconfig_usage",
}


async def _is_admin(context: ContextTypes.DEFAULT_TYPE, user_id: int) -> bool:
    group_chat_id = context.bot_data["group_chat_id"]
    return await is_group_admin(context.bot, group_chat_id, user_id)


def _render_table(lang: str, config: dict[PixelStage, stage_config.StageSettings]) -> str:
    stage_header = i18n.t("stageconfig.table_header_stage", lang)
    width_header = i18n.t("stageconfig.table_header_width", lang)
    guesses_header = i18n.t("stageconfig.table_header_guesses", lang)
    header = f"{stage_header:<8}{width_header:<8}{guesses_header}"
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


def _parse_all_stages(args: list[str]) -> dict[PixelStage, tuple[int, int]] | None:
    """/setstageconfig's `w:l` pair per stage, or None if any is malformed."""
    if len(args) != len(PixelStage):
        return None
    changes: dict[PixelStage, tuple[int, int]] = {}
    for stage, arg in zip(PixelStage, args, strict=True):
        parsed = _parse_pair(arg)
        if parsed is None:
            return None
        changes[stage] = parsed
    return changes


def _parse_single_stage(args: list[str]) -> dict[PixelStage, tuple[int, int]] | None:
    """/setstage's `<stage> <width> <limit>`, or None if malformed."""
    if len(args) != 3:
        return None
    try:
        stage_number, width, limit = int(args[0]), int(args[1]), int(args[2])
    except ValueError:
        return None
    if not (1 <= stage_number <= len(game_service.STAGE_ORDER)) or width <= 0 or limit <= 0:
        return None
    return {game_service.STAGE_ORDER[stage_number - 1]: (width, limit)}


async def _reply_usage(message: Message, lang: str, command: str, args: list[str]) -> None:
    logger.info(
        "sent invalid /{command} args {args!r} — replied with usage",
        command=command,
        args=args,
    )
    await message.reply_text(i18n.t(_USAGE_KEYS[command], lang))


async def _admin_dm(
    update: Update, context: ContextTypes.DEFAULT_TYPE, command: str
) -> tuple[Message, str] | None:
    """Shared DM + admin gate for all three commands: the message and
    bot language once it has passed, None after replying "admins only"
    (or silently, outside a DM)."""
    message = update.message
    user = update.effective_user
    if not is_private_chat(update) or message is None or user is None:
        return None
    with session_scope(context.bot_data["session_factory"]) as session:
        lang = settings.get_language(session)
    if not await _is_admin(context, user.id):
        logger.warning("non-admin tried /{command}", command=command)
        await message.reply_text(i18n.t("commands.admins_only", lang))
        return None
    return message, lang


async def stageconfig_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    gate = await _admin_dm(update, context, "stageconfig")
    if gate is None:
        return
    message, lang = gate
    with session_scope(context.bot_data["session_factory"]) as session:
        config = stage_config.get_stage_configs(session)

    logger.info("viewed /stageconfig")
    await message.reply_text(_render_table(lang, config), parse_mode="HTML")


_StageParser = Callable[[list[str]], dict[PixelStage, tuple[int, int]] | None]


async def _set_command(
    update: Update, context: ContextTypes.DEFAULT_TYPE, command: str, parse: _StageParser
) -> None:
    """Shared body of /setstageconfig and /setstage — they differ only in
    how their arguments parse into per-stage changes."""
    gate = await _admin_dm(update, context, command)
    if gate is None:
        return
    message, lang = gate
    args = context.args or []
    changes = parse(args)
    if changes is None:
        await _reply_usage(message, lang, command, args)
        return
    await _apply_stage_changes(message, context, lang, changes)


async def setstageconfig_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await _set_command(update, context, "setstageconfig", _parse_all_stages)


async def setstage_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await _set_command(update, context, "setstage", _parse_single_stage)


async def _apply_stage_changes(
    message: Message,
    context: ContextTypes.DEFAULT_TYPE,
    lang: str,
    changes: dict[PixelStage, tuple[int, int]],
) -> None:
    """Shared tail end of both setters: blocks while a game is running
    (offering to stop it), otherwise applies every change, replies with
    the updated table, then sends a visual preview per changed stage."""
    session_factory = context.bot_data["session_factory"]
    with session_scope(session_factory) as session:
        running = game_service.active_or_setup_game(session)
        if running is not None:
            logger.warning(
                "stage config edit blocked — a game is {status}",
                status=running.status,
                game_id=running.id,
            )
            await message.reply_text(
                i18n.t("stageconfig.game_running", lang),
                reply_markup=stop_confirm_keyboard(
                    lang, can_reveal=game_service.has_answer_to_reveal(running)
                ),
            )
            return

        for stage, (width, limit) in changes.items():
            stage_config.set_stage_config(
                session, stage, target_width=width, wrong_guess_limit=limit
            )
        config = stage_config.get_stage_configs(session)

    logger.info(
        "changed stage config: {changes}",
        changes=", ".join(
            f"{game_service.stage_label(stage)} width={width} limit={limit}"
            for stage, (width, limit) in changes.items()
        ),
    )
    await message.reply_text(_render_table(lang, config), parse_mode="HTML")
    await _send_preview(message, context, lang, changes)


async def _send_preview(
    message: Message,
    context: ContextTypes.DEFAULT_TYPE,
    lang: str,
    changes: dict[PixelStage, tuple[int, int]],
) -> None:
    chat_id = message.chat_id
    thread_id = message.message_thread_id
    for stage in sorted(changes, key=lambda s: s.name):
        width, limit = changes[stage]
        media = []
        for index, image_path in enumerate(_EXAMPLE_IMAGES):
            if not image_path.exists():
                logger.warning(
                    "stageconfig preview: missing example image {path}", path=str(image_path)
                )
                continue
            pixelated = pixelate(image_path.read_bytes(), width, DEFAULT_ALGORITHM)
            caption = (
                i18n.t(
                    "stageconfig.preview_caption", lang, stage=stage.name, width=width, limit=limit
                )
                if index == 0
                else None
            )
            media.append(InputMediaPhoto(media=pixelated, caption=caption))
        if media:
            await context.bot.send_media_group(
                chat_id=chat_id, message_thread_id=thread_id, media=media
            )
