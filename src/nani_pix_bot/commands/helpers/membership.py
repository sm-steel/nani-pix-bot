"""Group-membership check shared by DM-flow entry points.

Topic-scoped group commands (`/guess`, `/correct`, `/skip`,
`/leaderboard`) are already implicitly restricted to group members —
Telegram doesn't let anyone post into a group they're not part of. The
DM game-setup flow has no such structural guarantee (anyone can DM the
bot), so it checks membership explicitly via this helper.
"""

from telegram import Bot, ChatMemberRestricted
from telegram.constants import ChatMemberStatus
from telegram.error import TelegramError

_MEMBER_STATUSES = {
    ChatMemberStatus.OWNER,
    ChatMemberStatus.ADMINISTRATOR,
    ChatMemberStatus.MEMBER,
}
_ADMIN_STATUSES = {ChatMemberStatus.OWNER, ChatMemberStatus.ADMINISTRATOR}


async def is_group_member(bot: Bot, group_chat_id: int, user_id: int) -> bool:
    """Whether `user_id` is currently a member of `group_chat_id`.

    `RESTRICTED` members can still be present in the chat (`is_member`) or
    have effectively left despite the restriction persisting — only the
    former counts. Any Telegram API error (e.g. the user is unknown to
    this chat entirely) is treated as "not a member".
    """
    try:
        member = await bot.get_chat_member(group_chat_id, user_id)
    except TelegramError:
        return False

    if member.status in _MEMBER_STATUSES:
        return True
    if isinstance(member, ChatMemberRestricted):
        return member.is_member
    return False


async def is_group_admin(bot: Bot, group_chat_id: int, user_id: int) -> bool:
    """Whether `user_id` is currently an owner/administrator of
    `group_chat_id` — used to gate `/language`. Any Telegram API error is
    treated as "not an admin"."""
    try:
        member = await bot.get_chat_member(group_chat_id, user_id)
    except TelegramError:
        return False
    return member.status in _ADMIN_STATUSES
