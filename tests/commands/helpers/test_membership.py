from unittest.mock import AsyncMock, MagicMock

from telegram import ChatMemberRestricted
from telegram.constants import ChatMemberStatus
from telegram.error import TelegramError

from nani_pix_bot.commands.helpers.membership import is_group_admin, is_group_member


def _bot(*, status: str | None = None, is_member: bool | None = None, raises: bool = False):
    bot = MagicMock()
    if raises:
        bot.get_chat_member = AsyncMock(side_effect=TelegramError("user not found"))
        return bot
    spec = ChatMemberRestricted if status == ChatMemberStatus.RESTRICTED else None
    member = MagicMock(spec=spec)
    member.status = status
    member.is_member = is_member
    bot.get_chat_member = AsyncMock(return_value=member)
    return bot


async def test_owner_is_a_member() -> None:
    bot = _bot(status=ChatMemberStatus.OWNER)
    assert await is_group_member(bot, 555, 1) is True


async def test_administrator_is_a_member() -> None:
    bot = _bot(status=ChatMemberStatus.ADMINISTRATOR)
    assert await is_group_member(bot, 555, 1) is True


async def test_plain_member_is_a_member() -> None:
    bot = _bot(status=ChatMemberStatus.MEMBER)
    assert await is_group_member(bot, 555, 1) is True


async def test_restricted_but_still_present_is_a_member() -> None:
    bot = _bot(status=ChatMemberStatus.RESTRICTED, is_member=True)
    assert await is_group_member(bot, 555, 1) is True


async def test_restricted_and_no_longer_present_is_not_a_member() -> None:
    bot = _bot(status=ChatMemberStatus.RESTRICTED, is_member=False)
    assert await is_group_member(bot, 555, 1) is False


async def test_left_is_not_a_member() -> None:
    bot = _bot(status=ChatMemberStatus.LEFT)
    assert await is_group_member(bot, 555, 1) is False


async def test_banned_is_not_a_member() -> None:
    bot = _bot(status=ChatMemberStatus.BANNED)
    assert await is_group_member(bot, 555, 1) is False


async def test_unknown_to_the_chat_is_not_a_member() -> None:
    bot = _bot(raises=True)
    assert await is_group_member(bot, 555, 1) is False


async def test_owner_is_a_group_admin() -> None:
    bot = _bot(status=ChatMemberStatus.OWNER)
    assert await is_group_admin(bot, 555, 1) is True


async def test_administrator_is_a_group_admin() -> None:
    bot = _bot(status=ChatMemberStatus.ADMINISTRATOR)
    assert await is_group_admin(bot, 555, 1) is True


async def test_plain_member_is_not_a_group_admin() -> None:
    bot = _bot(status=ChatMemberStatus.MEMBER)
    assert await is_group_admin(bot, 555, 1) is False


async def test_unknown_to_the_chat_is_not_a_group_admin() -> None:
    bot = _bot(raises=True)
    assert await is_group_admin(bot, 555, 1) is False
