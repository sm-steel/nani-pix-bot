"""A player's current Telegram profile photo, for announcement cards.
Bots only see photos the user's privacy settings allow; anything missing
or failing yields None and the card draws initials instead (spec §7)."""

from loguru import logger
from telegram import Bot
from telegram.error import TelegramError


async def fetch_avatar(bot: Bot, user_id: int) -> bytes | None:
    try:
        photos = await bot.get_user_profile_photos(user_id, limit=1)
        if not photos.photos:
            logger.debug("no visible profile photo for {player_id}", player_id=user_id)
            return None
        file = await bot.get_file(photos.photos[0][-1].file_id)
        return bytes(await file.download_as_bytearray())
    except TelegramError as exc:
        logger.warning(
            "couldn't fetch the avatar of {player}: {error}",
            player=user_id,
            player_id=user_id,
            error=exc,
        )
        return None
