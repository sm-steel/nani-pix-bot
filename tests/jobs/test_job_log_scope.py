from unittest.mock import MagicMock

from nani_pix_bot import log_context
from nani_pix_bot.jobs.timers._shared import job_log_scope


def _context(*, name: str = "game-timeout-88", data: object = 88) -> MagicMock:
    context = MagicMock()
    context.job.name = name
    context.job.data = data
    return context


async def test_job_scope_replaces_an_inherited_context() -> None:
    """Issue #230: a job task inherits whatever context scheduled it (e.g.
    the update that started the game), so it must start from scratch."""
    seen: list[dict] = []

    @job_log_scope("game_id")
    async def callback(context) -> None:
        seen.append(log_context.current())

    log_context.reset(user_id=2, username="bob", update_id=501)
    await callback(_context())

    assert seen == [{"job": "game-timeout-88", "game_id": 88}]


async def test_job_scope_without_a_data_key_only_names_the_job() -> None:
    seen: list[dict] = []

    @job_log_scope()
    async def callback(context) -> None:
        seen.append(log_context.current())

    await callback(_context(name="idle-autostart", data=None))

    assert seen == [{"job": "idle-autostart"}]


async def test_job_scope_skips_a_missing_data_value() -> None:
    seen: list[dict] = []

    @job_log_scope("player_id")
    async def callback(context) -> None:
        seen.append(log_context.current())

    await callback(_context(name="turn-reminder", data=None))

    assert seen == [{"job": "turn-reminder"}]
