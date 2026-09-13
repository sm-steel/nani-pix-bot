"""Liveness heartbeat for the getUpdates polling loop, written to disk
so a Docker HEALTHCHECK can detect it going stale (see
docker-compose.yml's `bot` service and the ops vault's
`инфраструктура/Autoheal.md`).

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
on the line after a real call actually returns successfully."""

import asyncio
from pathlib import Path

from telegram.ext import Application

HEARTBEAT_PATH = Path("/tmp/nani-pix-bot-heartbeat")  # noqa: S108 - container-local, not shared


def install(application: Application, *, heartbeat_path: Path = HEARTBEAT_PATH) -> None:
    """Wraps `application.bot.get_updates` so `heartbeat_path` is only
    touched after a real call returns without raising. If the call
    raises (pool timeout, ConnectTimeout, Conflict, anything else), the
    file simply stops updating and goes stale — which is exactly what
    the Docker healthcheck watches for."""
    original_get_updates = application.bot.get_updates

    async def get_updates_with_heartbeat(*args, **kwargs):
        result = await original_get_updates(*args, **kwargs)
        await asyncio.to_thread(heartbeat_path.touch)
        return result

    application.bot.get_updates = get_updates_with_heartbeat
    heartbeat_path.touch()  # a first heartbeat right away, before polling has even started
