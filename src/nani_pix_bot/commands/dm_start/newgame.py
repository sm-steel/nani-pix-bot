"""`/newgame` — the screenshot-less entry point into the setup flow:
skips straight to identification (AniList/Shikimori/Tenrai/TMDB/manual),
picking a screenshot later instead of uploading one first. See
MECHANICS.md's "Starting a game" section.

Mirrors `intake.py`'s `photo_handler` role for the traditional,
photo-first entry point — both funnel into the same shared
`_start_new_game()` helper, just with (`None`) or without a real image
already in hand."""

from telegram import Update
from telegram.ext import ContextTypes

from nani_pix_bot.commands.dm_start._shared import _start_new_game
from nani_pix_bot.commands.helpers.scoping import is_private_chat


async def newgame_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """DM-only, same eligibility checks as the photo-first entry point
    (membership/games_enabled/can_start), via the shared helper — which
    also answers a second `/newgame` sent mid-setup by re-showing the
    step the starter is on, rather than telling them it isn't their
    turn (see `_resume_setup`)."""
    message = update.message
    if not is_private_chat(update) or message is None:
        return
    user = update.effective_user
    if user is None:
        return

    session_factory = context.bot_data["session_factory"]
    await _start_new_game(message, context, session_factory, user)
