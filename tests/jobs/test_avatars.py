from unittest.mock import AsyncMock, MagicMock

from telegram.error import TelegramError

from nani_pix_bot.jobs import avatars


async def test_fetch_avatar_downloads_the_largest_size_of_the_first_photo() -> None:
    small, large = MagicMock(file_id="s"), MagicMock(file_id="L")
    bot = MagicMock()
    bot.get_user_profile_photos = AsyncMock(return_value=MagicMock(photos=[[small, large]]))
    file = MagicMock()
    file.download_as_bytearray = AsyncMock(return_value=bytearray(b"img"))
    bot.get_file = AsyncMock(return_value=file)

    assert await avatars.fetch_avatar(bot, 5) == b"img"
    bot.get_file.assert_awaited_once_with("L")


async def test_no_photo_or_an_error_gives_none(records) -> None:
    bot = MagicMock()
    bot.get_user_profile_photos = AsyncMock(return_value=MagicMock(photos=[]))
    assert await avatars.fetch_avatar(bot, 5) is None

    bot.get_user_profile_photos = AsyncMock(side_effect=TelegramError("privacy"))
    assert await avatars.fetch_avatar(bot, 5) is None
    assert any(level == "WARNING" for level, _ in records)
