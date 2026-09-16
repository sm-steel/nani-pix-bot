"""Liveness heartbeat for the getUpdates polling loop, written to disk
so a Docker HEALTHCHECK can detect it going stale (see
docker-compose.yml's `bot` service).

This exists because of a real incident (2026-09-13): a single `httpx`
connection failing mid-TLS-handshake left python-telegram-bot's
getUpdates connection pool (which defaults to a pool size of 1 —
see app.py) permanently wedged. The bot kept running, retrying every
~1s, but never received another Telegram update for ~2.5 hours, and
none of it logged above DEBUG level.

Deliberately NOT a periodic `JobQueue` job: `JobQueue` runs on its own
scheduler, a separate asyncio task from the `Updater`'s polling loop —
a stuck `httpx` pool wait doesn't block the event loop, so a periodic
job would keep firing (and reporting "healthy") straight through an
outage exactly like that one. Instead, `install()` wraps the bot's
real `get_updates` call itself, so the heartbeat file is only touched
on the line after a real call actually returns successfully.

The first version shipped patching the *instance*
(`application.bot.get_updates = ...`) and crash-looped in production
within seconds: `TelegramObject.__setattr__` freezes every attribute
assignment on a live instance once constructed, raising
"AttributeError: Attribute `get_updates` of class `ExtBot` can't be
set!". Patching the *class* instead
(`type(application.bot).get_updates = ...`) bypasses that guard
entirely — it's `type.__setattr__` mutating the class's own namespace,
not `TelegramObject`'s instance-level override — verified directly
against a real `ApplicationBuilder`-built bot, not just inferred from
reading the freeze check's source."""

import asyncio
from pathlib import Path

from telegram.ext import Application

HEARTBEAT_PATH = Path("/tmp/nani-pix-bot-heartbeat")  # noqa: S108 - container-local, not shared


def install(application: Application, *, heartbeat_path: Path = HEARTBEAT_PATH) -> None:
    """Wraps `get_updates` on the bot's *class* (see module docstring
    for why not the instance) so `heartbeat_path` is only touched after
    a real call returns without raising. If the call raises (pool
    timeout, ConnectTimeout, Conflict, anything else), the file simply
    stops updating and goes stale — which is exactly what the Docker
    healthcheck watches for."""
    bot_class = type(application.bot)
    original_get_updates = bot_class.get_updates

    async def get_updates_with_heartbeat(self, *args, **kwargs):
        result = await original_get_updates(self, *args, **kwargs)
        await asyncio.to_thread(heartbeat_path.touch)
        return result

    bot_class.get_updates = get_updates_with_heartbeat
    heartbeat_path.touch()  # a first heartbeat right away, before polling has even started
