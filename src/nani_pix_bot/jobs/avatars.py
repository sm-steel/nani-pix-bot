"""A player's current Telegram profile photo, for announcement cards.
Bots only see photos the user's privacy settings allow; anything missing
or failing yields None and the card draws initials instead (spec §7).

While a Nopperabo hex is on the player the photo is never fetched: the
faceless art stands in for it. Every image that draws a profile photo goes
through `fetch_avatar`, so that is the one place the curse is applied."""

from datetime import UTC, datetime
from functools import cache
from pathlib import Path

from loguru import logger
from sqlalchemy.orm import Session, sessionmaker
from telegram import Bot
from telegram.error import TelegramError

from nani_pix_bot.db import session_scope
from nani_pix_bot.services.seasons import hex_marks

FACELESS_AVATAR = Path(__file__).resolve().parents[1] / "assets" / "icons" / "hex"
FACELESS_AVATAR = FACELESS_AVATAR / "nopperabo_avatar.jpg"


@cache
def _faceless_art() -> bytes | None:
    """The faceless art, read once; a missing file logs once and gives None."""
    try:
        return FACELESS_AVATAR.read_bytes()
    except OSError as exc:
        logger.error(
            "the faceless avatar art {path} is unreadable: {error} — using real photos",
            path=str(FACELESS_AVATAR),
            error=exc,
        )
        return None


def _is_faceless(session_factory: sessionmaker[Session], user_id: int) -> bool:
    with session_scope(session_factory) as session:
        return hex_marks.is_faceless(session, user_id, datetime.now(UTC))


async def fetch_avatar(
    bot: Bot, user_id: int, session_factory: sessionmaker[Session]
) -> bytes | None:
    if _is_faceless(session_factory, user_id):
        art = _faceless_art()
        if art is not None:
            logger.debug("nopperabo: faceless art for {player_id}", player_id=user_id)
            return art
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
