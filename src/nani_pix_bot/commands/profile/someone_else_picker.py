"""👥 Someone else under your own /profile card (issue #479): a player picker,
and a pick sends that player's card the way `/profile @user` does
(helpers/someone_else.py)."""

from telegram import Message, Update
from telegram.constants import ChatType
from telegram.ext import ContextTypes

from nani_pix_bot.commands.helpers import someone_else
from nani_pix_bot.commands.helpers.player_picker import PickerSpec
from nani_pix_bot.commands.profile.data import SOMEONE_ELSE_PREFIX
from nani_pix_bot.commands.profile.send import CardRequest, send_card


async def _show(
    message: Message, context: ContextTypes.DEFAULT_TYPE, viewer_id: int, target_id: int
) -> None:
    private = message.chat.type == ChatType.PRIVATE
    await send_card(message, context, CardRequest(viewer_id, target_id, private, quote=False))


SOMEONE_ELSE = someone_else.SomeoneElse(
    PickerSpec(SOMEONE_ELSE_PREFIX, "/profile someone-else"), "profile", _show
)


async def profile_someone_else_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await someone_else.callback(update, context, SOMEONE_ELSE)
