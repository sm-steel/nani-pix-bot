"""`remember_user` — the one place a `Player` row gets created from
simply *seeing* someone, wired in app.py as a `TypeHandler` in group -1
so it runs on every update before any command handler.

Why a global pre-handler rather than a `get_or_create_player` call in
each command: `/correct @username` and `/skip @username` resolve a typed
handle against the `players` table, and before this existed the table was
only populated by a successful `/guess` or by starting a game in DM.
`/correct` exists precisely for someone who answered in plain prose or
whom the fuzzy matcher missed — exactly the person who had never been
recorded. That was a live failure on 2026-09-14: three consecutive
`/correct @<player>` attempts all refused while that player was actively
chatting in the topic and had already DM'd the bot `/start`, neither of
which registered anything. Scattering the call through every handler
would fix it for a while and then drift the next time a command is
added."""

from telegram import Update
from telegram.ext import ContextTypes

from nani_pix_bot.db import session_scope
from nani_pix_bot.services import players


async def remember_user(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Create-or-refresh the `Player` row for whoever this update came
    from. Deliberately quiet: `get_or_create_player` logs the genuinely
    interesting case (a person seen for the first time) and says nothing
    for the far more common refresh, since this runs on literally every
    update."""
    user = update.effective_user
    if user is None or user.is_bot:
        return

    session_factory = context.bot_data["session_factory"]
    with session_scope(session_factory) as session:
        players.get_or_create_player(session, user.id, username=user.username)
