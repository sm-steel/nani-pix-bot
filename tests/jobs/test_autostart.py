from datetime import UTC, datetime, timedelta
from typing import cast
from unittest.mock import AsyncMock, MagicMock

import pytest
from telegram.ext import ContextTypes

from nani_pix_bot.jobs.timers import autostart as autostart_timers
from nani_pix_bot.models.bot_settings import BotSettings
from nani_pix_bot.models.enums import GameStatus, Provider
from nani_pix_bot.models.game import Game
from nani_pix_bot.models.player import Player
from nani_pix_bot.models.turn_state import TurnState
from nani_pix_bot.services import game as game_service
from nani_pix_bot.services import i18n
from nani_pix_bot.services.game import autostart as autostart_service
from nani_pix_bot.services.game.autostart import AnimePick, GatheredPick, ScreenshotPick
from nani_pix_bot.services.search.shikimori import ShikimoriResult


def _fake_pick() -> GatheredPick:
    """A GatheredPick with placeholder (non-image) screenshot pair bytes
    — every test using it also monkeypatches pixelate.pixelate, or never
    reaches it (the two new run_bot_autostart-abort tests below return
    False before pixelation)."""
    return GatheredPick(
        anime=AnimePick(
            result=ShikimoriResult(1, "Frieren", None, None, []), source=Provider.SHIKIMORI
        ),
        screenshot=ScreenshotPick(
            provider=Provider.SHIKIMORI, provider_id=1, image_bytes_a=b"x", image_bytes_b=b"y"
        ),
    )


def _expected_first_turn_caption(key: str, **kwargs) -> str:
    """The caption `_build_first_turn_post` computes for a freshly
    activated hard-mode game (hard_mode_turn == 1, wrong_guess_count ==
    0) — same "compute it the same way production does" convention this
    codebase's other i18n-asserting tests already use (see e.g.
    tests/commands/test_version.py), which stays correct symmetrically
    whether or not Task 9's real translated text has landed yet (a
    missing key just makes both sides equal the bare key)."""
    return i18n.t(
        key,
        "en",
        turn=1,
        total=game_service.HARD_MODE_TURN_COUNT,
        remaining=game_service.HARD_MODE_WRONG_GUESS_LIMIT,
        limit=game_service.HARD_MODE_WRONG_GUESS_LIMIT,
        **kwargs,
    )


def test_schedule_idle_autostart_calls_run_once_from_the_stored_deadline() -> None:
    job_queue = MagicMock()
    job_queue.get_jobs_by_name.return_value = []
    turn_state = TurnState(id=1, autostart_deadline_at=datetime.now(UTC) + timedelta(hours=24))

    autostart_timers.schedule_idle_autostart(job_queue, turn_state)

    job_queue.run_once.assert_called_once()
    _, kwargs = job_queue.run_once.call_args
    assert kwargs["name"] == autostart_timers.IDLE_AUTOSTART_JOB_NAME


def test_schedule_idle_autostart_is_a_noop_when_no_deadline_is_set() -> None:
    job_queue = MagicMock()
    job_queue.get_jobs_by_name.return_value = []
    turn_state = TurnState(id=1, autostart_deadline_at=None)

    autostart_timers.schedule_idle_autostart(job_queue, turn_state)

    job_queue.run_once.assert_not_called()


def test_cancel_idle_autostart_removes_the_named_job() -> None:
    job = MagicMock()
    job_queue = MagicMock()
    job_queue.get_jobs_by_name.return_value = [job]

    autostart_timers.cancel_idle_autostart(job_queue)

    job_queue.get_jobs_by_name.assert_called_once_with(autostart_timers.IDLE_AUTOSTART_JOB_NAME)
    job.schedule_removal.assert_called_once()


def _make_context(session_factory, *, bot_id: int = 999) -> MagicMock:
    context = MagicMock()
    context.bot_data = {
        "session_factory": session_factory,
        "group_chat_id": 555,
        "game_topic_id": 7,
        "search_client": MagicMock(),
        "tmdb_client": MagicMock(),
        "bot_username": "nani_pix_bot",
    }
    context.bot.id = bot_id
    context.bot.send_media_group = AsyncMock(
        return_value=[MagicMock(message_id=998), MagicMock(message_id=999)]
    )
    context.bot.pin_chat_message = AsyncMock()
    context.bot.unpin_chat_message = AsyncMock()
    context.job_queue = MagicMock()
    context.job_queue.get_jobs_by_name.return_value = []
    return context


async def test_idle_autostart_job_callback_noops_when_the_turn_is_no_longer_open(
    session_factory,
) -> None:
    with session_factory() as session:
        session.add(Player(telegram_user_id=1))
        session.add(TurnState(id=1, next_starter_id=1))
        session.commit()
    context = _make_context(session_factory)

    await autostart_timers.idle_autostart_job_callback(cast(ContextTypes.DEFAULT_TYPE, context))

    context.bot.send_media_group.assert_not_awaited()


async def test_idle_autostart_job_callback_noops_when_a_game_is_already_running(
    session_factory,
) -> None:
    with session_factory() as session:
        session.add(Player(telegram_user_id=1))
        session.add(TurnState(id=1, next_starter_id=None))
        session.add(Game(starter_id=1, status=GameStatus.SETUP))
        session.commit()
    context = _make_context(session_factory)

    await autostart_timers.idle_autostart_job_callback(cast(ContextTypes.DEFAULT_TYPE, context))

    context.bot.send_media_group.assert_not_awaited()


async def test_idle_autostart_job_callback_noops_when_disabled(session_factory) -> None:
    with session_factory() as session:
        session.add(TurnState(id=1, next_starter_id=None))
        session.add(BotSettings(id=1, games_enabled=True, autostart_enabled=False))
        session.commit()
    context = _make_context(session_factory)

    await autostart_timers.idle_autostart_job_callback(cast(ContextTypes.DEFAULT_TYPE, context))

    context.bot.send_media_group.assert_not_awaited()


async def test_idle_autostart_job_callback_reschedules_on_a_failed_pick(
    session_factory, monkeypatch: pytest.MonkeyPatch
) -> None:
    with session_factory() as session:
        session.add(TurnState(id=1, next_starter_id=None))
        session.add(BotSettings(id=1, games_enabled=True, autostart_enabled=True))
        session.commit()
    context = _make_context(session_factory)

    async def failing_gather_pick(search_client, tmdb_client):
        return None

    monkeypatch.setattr(autostart_service, "gather_pick", failing_gather_pick)

    await autostart_timers.idle_autostart_job_callback(cast(ContextTypes.DEFAULT_TYPE, context))

    context.bot.send_media_group.assert_not_awaited()
    context.job_queue.run_once.assert_called_once()
    with session_factory() as session:
        turn_state = game_service.get_turn_state(session)
        assert turn_state is not None
        assert turn_state.autostart_deadline_at is not None


async def test_maybe_overthrow_does_nothing_when_the_roll_misses(
    session_factory, monkeypatch: pytest.MonkeyPatch
) -> None:
    with session_factory() as session:
        session.add(Player(telegram_user_id=1))
        session.add(TurnState(id=1, next_starter_id=1))
        session.add(BotSettings(id=1, games_enabled=True, autostart_enabled=True))
        session.commit()
    context = _make_context(session_factory)
    monkeypatch.setattr(autostart_service, "roll_overthrow", lambda: False)

    await autostart_timers.maybe_overthrow(
        cast(ContextTypes.DEFAULT_TYPE, context),
        session_factory,
        winner_id=1,
        winner_name="frieren",
    )

    context.bot.send_media_group.assert_not_awaited()


async def test_maybe_overthrow_claims_the_game_on_a_hit(
    session_factory, monkeypatch: pytest.MonkeyPatch
) -> None:
    with session_factory() as session:
        session.add(Player(telegram_user_id=1))
        session.add(TurnState(id=1, next_starter_id=1))
        session.add(BotSettings(id=1, games_enabled=True, autostart_enabled=True))
        session.commit()
    context = _make_context(session_factory)
    monkeypatch.setattr(autostart_service, "roll_overthrow", lambda: True)
    # A real pixelate() can't process fake screenshot bytes — same
    # monkeypatch tests/jobs/test_timers.py already uses for the same
    # reason.
    monkeypatch.setattr(
        "nani_pix_bot.services.pixelate.pixelate",
        lambda image_bytes, target_width, algorithm: b"pixelated",
    )

    async def fake_gather_pick(search_client, tmdb_client):
        return _fake_pick()

    monkeypatch.setattr(autostart_service, "gather_pick", fake_gather_pick)

    await autostart_timers.maybe_overthrow(
        cast(ContextTypes.DEFAULT_TYPE, context),
        session_factory,
        winner_id=1,
        winner_name="frieren",
    )

    context.bot.send_media_group.assert_awaited_once()
    _, kwargs = context.bot.send_media_group.call_args
    media = kwargs["media"]
    assert media[0].caption == _expected_first_turn_caption(
        "dm_start.hard_mode_game_started_caption_overthrow_winner", winner="frieren"
    )
    with session_factory() as session:
        turn_state = game_service.get_turn_state(session)
        assert turn_state is not None
        assert turn_state.next_starter_id is None


async def test_run_bot_autostart_cancels_the_idle_autostart_timer_on_success(
    session_factory, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Symmetric with _start_new_game() (commands/dm_start/_shared.py),
    which cancels both the DB deadline (clear_autostart) and the
    JobQueue job (cancel_idle_autostart) — run_bot_autostart previously
    only did the former, a theoretical gap closed defensively here even
    though no reachable path currently exercises it (see issue #159's
    final review)."""
    with session_factory() as session:
        session.add(Player(telegram_user_id=1))
        session.add(TurnState(id=1, next_starter_id=None))
        session.commit()
    context = _make_context(session_factory)
    monkeypatch.setattr(
        "nani_pix_bot.services.pixelate.pixelate",
        lambda image_bytes, target_width, algorithm: b"pixelated",
    )

    async def fake_gather_pick(search_client, tmdb_client):
        return _fake_pick()

    monkeypatch.setattr(autostart_service, "gather_pick", fake_gather_pick)
    cancel_calls = []
    monkeypatch.setattr(
        autostart_timers, "cancel_idle_autostart", lambda job_queue: cancel_calls.append(job_queue)
    )

    claim = autostart_timers._AutostartClaim(
        trigger=autostart_timers.AutostartTrigger.IDLE, dethroned_winner_name=None
    )
    started = await autostart_timers.run_bot_autostart(
        cast(ContextTypes.DEFAULT_TYPE, context), session_factory, claim
    )

    assert started is True
    assert cancel_calls == [context.job_queue]


async def test_run_bot_autostart_aborts_when_a_game_appeared_in_the_meantime(
    session_factory, monkeypatch: pytest.MonkeyPatch
) -> None:
    """gather_pick() is a multi-attempt, real-HTTP-round-trip call — long
    enough for a human to have started a game by the time its result
    comes back. The post-pick session block must re-check and abort
    rather than create a second SETUP/ACTIVE game."""
    with session_factory() as session:
        session.add(Player(telegram_user_id=1))
        session.add(TurnState(id=1, next_starter_id=None))
        session.add(Game(starter_id=1, status=GameStatus.SETUP))
        session.commit()
    context = _make_context(session_factory)

    async def fake_gather_pick(search_client, tmdb_client):
        return _fake_pick()

    monkeypatch.setattr(autostart_service, "gather_pick", fake_gather_pick)

    claim = autostart_timers._AutostartClaim(
        trigger=autostart_timers.AutostartTrigger.IDLE, dethroned_winner_name=None
    )
    started = await autostart_timers.run_bot_autostart(
        cast(ContextTypes.DEFAULT_TYPE, context), session_factory, claim
    )

    assert started is False
    context.bot.send_media_group.assert_not_awaited()


async def test_run_bot_autostart_aborts_when_the_turn_was_claimed_in_the_meantime(
    session_factory, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Same race as above, but via /skip @user designating next_starter_id
    instead of an outright game start — activate_game() would otherwise
    silently wipe that designation back to None (issue found in review)."""
    with session_factory() as session:
        session.add(Player(telegram_user_id=1))
        session.add(TurnState(id=1, next_starter_id=1))
        session.commit()
    context = _make_context(session_factory)

    async def fake_gather_pick(search_client, tmdb_client):
        return _fake_pick()

    monkeypatch.setattr(autostart_service, "gather_pick", fake_gather_pick)

    claim = autostart_timers._AutostartClaim(
        trigger=autostart_timers.AutostartTrigger.IDLE, dethroned_winner_name=None
    )
    started = await autostart_timers.run_bot_autostart(
        cast(ContextTypes.DEFAULT_TYPE, context), session_factory, claim
    )

    assert started is False
    context.bot.send_media_group.assert_not_awaited()
    with session_factory() as session:
        turn_state = game_service.get_turn_state(session)
        assert turn_state is not None
        assert turn_state.next_starter_id == 1


async def test_run_bot_autostart_creates_a_hard_mode_game_and_posts_both_images(
    session_factory, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Every autostart pick is hard mode now (no `if` in production
    code): both screenshot halves land in hard_mode_image_a/_b — not
    original_image, which stays None — and the group post pixelates
    both halves at HARD_MODE_TURN_WIDTHS[1], the width for a freshly
    activated game's first turn, with the idle caption on the first
    photo only."""
    with session_factory() as session:
        session.add(Player(telegram_user_id=1))
        session.add(TurnState(id=1, next_starter_id=None))
        session.commit()
    context = _make_context(session_factory)

    pixelate_calls: list[tuple[bytes, int]] = []

    def fake_pixelate(image_bytes, target_width, algorithm):
        pixelate_calls.append((image_bytes, target_width))
        return image_bytes + b"-pixelated"

    monkeypatch.setattr("nani_pix_bot.services.pixelate.pixelate", fake_pixelate)

    async def fake_gather_pick(search_client, tmdb_client):
        return _fake_pick()

    monkeypatch.setattr(autostart_service, "gather_pick", fake_gather_pick)

    claim = autostart_timers._AutostartClaim(
        trigger=autostart_timers.AutostartTrigger.IDLE, dethroned_winner_name=None
    )
    started = await autostart_timers.run_bot_autostart(
        cast(ContextTypes.DEFAULT_TYPE, context), session_factory, claim
    )

    assert started is True
    assert pixelate_calls == [
        (b"x", game_service.HARD_MODE_TURN_WIDTHS[1]),
        (b"y", game_service.HARD_MODE_TURN_WIDTHS[1]),
    ]

    context.bot.send_media_group.assert_awaited_once()
    _, kwargs = context.bot.send_media_group.call_args
    media = kwargs["media"]
    assert len(media) == 2
    assert media[0].media.input_file_content == b"x-pixelated"
    assert media[1].media.input_file_content == b"y-pixelated"
    assert media[0].caption == _expected_first_turn_caption(
        "dm_start.hard_mode_game_started_caption_idle"
    )
    assert media[1].caption is None

    with session_factory() as session:
        game = session.query(Game).one()
        assert game.hard_mode is True
        assert game.hard_mode_image_a == b"x"
        assert game.hard_mode_image_b == b"y"
        assert game.original_image is None


async def test_run_bot_autostart_uses_the_overthrow_open_caption_when_no_winner_was_dethroned(
    session_factory, monkeypatch: pytest.MonkeyPatch
) -> None:
    """maybe_overthrow's OVERTHROW trigger with no dethroned_winner_name
    (an unsolved/timeout ending that left the turn open) selects the
    third caption-key branch — distinct from both the IDLE branch (see
    the combined test above) and the overthrow-winner branch (see
    test_maybe_overthrow_claims_the_game_on_a_hit)."""
    with session_factory() as session:
        session.add(Player(telegram_user_id=1))
        session.add(TurnState(id=1, next_starter_id=None))
        session.commit()
    context = _make_context(session_factory)
    monkeypatch.setattr(
        "nani_pix_bot.services.pixelate.pixelate",
        lambda image_bytes, target_width, algorithm: b"pixelated",
    )

    async def fake_gather_pick(search_client, tmdb_client):
        return _fake_pick()

    monkeypatch.setattr(autostart_service, "gather_pick", fake_gather_pick)

    claim = autostart_timers._AutostartClaim(
        trigger=autostart_timers.AutostartTrigger.OVERTHROW, dethroned_winner_name=None
    )
    started = await autostart_timers.run_bot_autostart(
        cast(ContextTypes.DEFAULT_TYPE, context), session_factory, claim
    )

    assert started is True
    _, kwargs = context.bot.send_media_group.call_args
    media = kwargs["media"]
    assert media[0].caption == _expected_first_turn_caption(
        "dm_start.hard_mode_game_started_caption_overthrow_open"
    )
