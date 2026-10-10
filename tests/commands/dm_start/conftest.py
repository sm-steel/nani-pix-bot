from collections.abc import Coroutine, Iterator
from unittest.mock import AsyncMock

import pytest

from nani_pix_bot.commands.dm_start import aliases


@pytest.fixture(autouse=True)
def background_tasks(monkeypatch: pytest.MonkeyPatch) -> Iterator[list[Coroutine]]:
    """The alias searches handlers start (aliases.start_alias_search),
    collected instead of spawned so a test decides when they run — see
    run_background. The search itself finds nothing unless a test says
    otherwise, so no test reaches a real provider."""
    pending: list[Coroutine] = []
    monkeypatch.setattr(aliases, "_spawn", lambda _context, coroutine: pending.append(coroutine))
    monkeypatch.setattr(aliases.aliases, "find_aliases", AsyncMock(return_value=[]))
    yield pending
    for coroutine in pending:
        coroutine.close()


async def run_background(pending: list[Coroutine]) -> None:
    """Run every collected background task to completion, in order."""
    while pending:
        await pending.pop(0)
