"""TEMPORARY diagnostic command — DELETE ME (and its registration in
app.py, and the tmp/example*.* images) once pixelation width tuning is
done. /testpixels <width>[,<width>...] pixelates both example images at
each given width and posts them to the chat (group or DM) as one album
per width, so candidate widths can be compared visually before setting
them for real via /setstageconfig or /setstage. Open to anyone who can
message the bot — not worth i18n/test investment given its lifespan."""

from pathlib import Path

from loguru import logger
from telegram import InputMediaPhoto, Update
from telegram.ext import ContextTypes

from nani_pix_bot.services.pixelate import pixelate

_EXAMPLE_IMAGES = [Path("tmp/example1.jpg"), Path("tmp/example2.png")]


def _parse_widths(args: list[str]) -> list[int] | None:
    tokens = [token for arg in args for token in arg.split(",") if token.strip()]
    if not tokens:
        return None
    try:
        widths = {int(token) for token in tokens}
    except ValueError:
        return None
    if any(width <= 0 for width in widths):
        return None
    return sorted(widths)


async def testpixels_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    message = update.message
    user = update.effective_user
    chat = update.effective_chat
    if message is None or user is None or chat is None:
        return

    widths = _parse_widths(context.args or [])
    if widths is None:
        await message.reply_text(
            "Usage: /testpixels <width>[,<width>...] e.g. /testpixels 32,48,128"
        )
        return

    chat_id = chat.id
    thread_id = message.message_thread_id

    for width in widths:
        media = []
        for index, image_path in enumerate(_EXAMPLE_IMAGES):
            if not image_path.exists():
                logger.warning("testpixels: missing example image {}", image_path)
                continue
            pixelated = pixelate(image_path.read_bytes(), width)
            caption = f"{width}px" if index == 0 else None
            media.append(InputMediaPhoto(media=pixelated, caption=caption))
        if media:
            await context.bot.send_media_group(
                chat_id=chat_id, message_thread_id=thread_id, media=media
            )

    logger.info("{} ran /testpixels with widths {}", user.id, widths)
