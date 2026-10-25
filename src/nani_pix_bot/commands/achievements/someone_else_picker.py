"""🔍 Someone else under your own /achievements list (issue #479): a player
picker, and a pick shows that player's achievements the way
`/achievements @user` does: the browser in DM, the summary in the game topic
(helpers/someone_else.py)."""

from telegram import Message, Update
from telegram.constants import ChatType
from telegram.ext import ContextTypes

from nani_pix_bot.commands.achievements import browser, post_summary
from nani_pix_bot.commands.achievements.common import SOMEONE_ELSE_PREFIX
from nani_pix_bot.commands.helpers import someone_else
from nani_pix_bot.commands.helpers.player_picker import PickerSpec


async def _show(
    message: Message, context: ContextTypes.DEFAULT_TYPE, viewer_id: int, target_id: int
) -> None:
    if message.chat.type == ChatType.PRIVATE:
        await browser.open_browser(message, context, browser.Browse(viewer_id, target_id))
    else:
        await post_summary(message, context, viewer_id, target_id)


SOMEONE_ELSE = someone_else.SomeoneElse(
    PickerSpec(SOMEONE_ELSE_PREFIX, "/achievements someone-else"), "achievements", _show
)


async def achievements_someone_else_callback(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    await someone_else.callback(update, context, SOMEONE_ELSE)
