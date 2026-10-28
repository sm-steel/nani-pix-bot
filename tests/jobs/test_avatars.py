import ast
import asyncio
import threading
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest
from telegram.error import TelegramError

from nani_pix_bot.jobs import avatars
from nani_pix_bot.models.enums import SeasonStatus
from nani_pix_bot.models.halloween import Hex
from nani_pix_bot.models.season import SeasonSchedule
from nani_pix_bot.seasons.mechanics import HexKind


async def test_fetch_avatar_downloads_the_largest_size_of_the_first_photo(
    session_factory,
) -> None:
    small, large = MagicMock(file_id="s"), MagicMock(file_id="L")
    bot = MagicMock()
    bot.get_user_profile_photos = AsyncMock(return_value=MagicMock(photos=[[small, large]]))
    file = MagicMock()
    file.download_as_bytearray = AsyncMock(return_value=bytearray(b"img"))
    bot.get_file = AsyncMock(return_value=file)

    assert await avatars.fetch_avatar(bot, 5, session_factory) == b"img"
    bot.get_file.assert_awaited_once_with("L")


async def test_no_photo_or_an_error_gives_none(records, session_factory) -> None:
    bot = MagicMock()
    bot.get_user_profile_photos = AsyncMock(return_value=MagicMock(photos=[]))
    assert await avatars.fetch_avatar(bot, 5, session_factory) is None

    bot.get_user_profile_photos = AsyncMock(side_effect=TelegramError("privacy"))
    assert await avatars.fetch_avatar(bot, 5, session_factory) is None
    assert any(level == "WARNING" for level, _ in records)


SRC = Path(avatars.__file__).resolve().parents[1]
REAL = b"real-photo"


def _bot() -> MagicMock:
    bot = MagicMock()
    bot.get_user_profile_photos = AsyncMock(return_value=MagicMock(photos=[[MagicMock()]]))
    file = MagicMock()
    file.download_as_bytearray = AsyncMock(return_value=bytearray(REAL))
    bot.get_file = AsyncMock(return_value=file)
    return bot


def _hex(session_factory, kind: HexKind, *, expires: timedelta | None) -> None:
    now = datetime.now(UTC).replace(tzinfo=None)
    with session_factory() as session:
        season = SeasonSchedule(
            run_id="demo_3",
            start_at=now - timedelta(days=1),
            end_at=now + timedelta(days=9),
            status=SeasonStatus.ACTIVE,
            created_by=9,
        )
        session.add(season)
        session.flush()
        session.add(
            Hex(
                season_id=season.id,
                kind=kind.value,
                caster_id=1,
                target_id=2,
                cast_at=now - timedelta(hours=3),
                expires_at=None if expires is None else now + expires,
            )
        )
        session.commit()


@pytest.fixture(autouse=True)
def _fresh_art_cache():
    avatars._faceless_art.cache_clear()
    yield
    avatars._faceless_art.cache_clear()


async def test_a_nopperabo_player_gets_the_faceless_art(session_factory) -> None:
    _hex(session_factory, HexKind.NOPPERABO, expires=timedelta(hours=1))
    bot = _bot()

    art = await avatars.fetch_avatar(bot, 2, session_factory)

    assert art == avatars.FACELESS_AVATAR.read_bytes() != REAL
    bot.get_user_profile_photos.assert_not_called()
    assert await avatars.fetch_avatar(bot, 3, session_factory) == REAL  # not hexed


async def test_other_hexes_keep_the_real_photo(session_factory) -> None:
    _hex(session_factory, HexKind.KAPPA, expires=None)
    assert await avatars.fetch_avatar(_bot(), 2, session_factory) == REAL


async def test_an_expired_nopperabo_gives_the_photo_back(session_factory) -> None:
    _hex(session_factory, HexKind.NOPPERABO, expires=timedelta(hours=-1))
    assert await avatars.fetch_avatar(_bot(), 2, session_factory) == REAL


async def test_the_art_is_read_once(session_factory, monkeypatch) -> None:
    _hex(session_factory, HexKind.NOPPERABO, expires=timedelta(hours=1))
    reads: list[int] = []
    real_read = Path.read_bytes
    monkeypatch.setattr(Path, "read_bytes", lambda self: (reads.append(1), real_read(self))[1])
    for _ in range(3):
        await avatars.fetch_avatar(_bot(), 2, session_factory)
    assert len(reads) == 1


async def test_missing_art_logs_error_once_and_falls_back(
    session_factory, monkeypatch, log_records
) -> None:
    _hex(session_factory, HexKind.NOPPERABO, expires=timedelta(hours=1))
    monkeypatch.setattr(avatars, "FACELESS_AVATAR", Path("nowhere.jpg"))

    assert await avatars.fetch_avatar(_bot(), 2, session_factory) == REAL
    assert await avatars.fetch_avatar(_bot(), 2, session_factory) == REAL

    errors = [r for r in log_records if r.level == "ERROR"]
    assert len(errors) == 1


def test_every_caller_passes_a_session_factory() -> None:
    """Each image path draws the photo through fetch_avatar, so the curse covers all."""
    calls = 0
    for path in SRC.rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Call) and getattr(node.func, "id", None) == "fetch_avatar":
                calls += 1
                assert len(node.args) == 3, f"{path}:{node.lineno}"
            if isinstance(node, ast.Attribute) and node.attr == "get_user_profile_photos":
                assert path.name == "avatars.py", f"{path}:{node.lineno} bypasses the seam"
    assert calls >= 6


async def test_the_faceless_check_runs_off_the_event_loop(session_factory, monkeypatch) -> None:
    """The sync DB session must not block the loop (and with it AVATAR_TIMEOUT)."""
    threads: list[threading.Thread] = []
    real = avatars._is_faceless

    def spy(factory, user_id: int) -> bool:
        threads.append(threading.current_thread())
        return real(factory, user_id)

    monkeypatch.setattr(avatars, "_is_faceless", spy)
    _hex(session_factory, HexKind.NOPPERABO, expires=timedelta(hours=1))

    art = await avatars.fetch_avatar(_bot(), 2, session_factory)

    assert art == avatars.FACELESS_AVATAR.read_bytes()
    assert threads
    assert threads[0] is not threading.main_thread()


async def test_a_slow_faceless_check_still_times_out(session_factory, monkeypatch) -> None:
    release = threading.Event()

    def slow(_factory, _user_id: int) -> bool:
        release.wait(5)
        return False

    monkeypatch.setattr(avatars, "_is_faceless", slow)
    try:
        with pytest.raises(TimeoutError):
            await asyncio.wait_for(avatars.fetch_avatar(_bot(), 2, session_factory), 0.05)
    finally:
        release.set()
