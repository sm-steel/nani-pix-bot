from dataclasses import replace
from datetime import UTC, datetime, timedelta
from io import BytesIO

import pytest
from PIL import Image

from nani_pix_bot.models.enums import SeasonStatus
from nani_pix_bot.models.player import Player
from nani_pix_bot.models.season import SeasonPass, SeasonResult, SeasonSchedule
from nani_pix_bot.seasons import registry
from nani_pix_bot.services.cards import backgrounds
from nani_pix_bot.services.cosmetics import profile

ALICE = 2
NOW = datetime(2026, 1, 1, tzinfo=UTC)


def _png(color: tuple[int, int, int, int]) -> bytes:
    buffer = BytesIO()
    Image.new("RGBA", (8, 8), color).save(buffer, format="PNG")
    return buffer.getvalue()


@pytest.fixture
def season_id(session) -> int:
    session.add(Player(telegram_user_id=ALICE, username="alice"))
    row = SeasonSchedule(
        run_id="demo_1",
        start_at=NOW,
        end_at=NOW + timedelta(days=5),
        status=SeasonStatus.ENDED,
        created_by=9,
    )
    session.add(row)
    session.flush()
    session.add(SeasonResult(season_id=row.id, player_id=ALICE, rank=1, xp=10))
    session.flush()
    return row.id


def _claimed(session, season_id: int, free: int, premium: int = 0) -> None:
    session.add(
        SeasonPass(season_id=season_id, player_id=ALICE, free_claimed=free, premium_claimed=premium)
    )
    session.flush()


def _stamp(session) -> profile.Stamp:
    (stamp,) = profile.build(session, ALICE, "EN").stamps
    return stamp


@pytest.fixture
def art_dir(monkeypatch, tmp_path):
    folder = tmp_path / "demo_1"
    folder.mkdir()
    (folder / "stamp.png").write_bytes(_png((1, 2, 3, 255)))
    (folder / "stamp_plus.png").write_bytes(_png((200, 180, 20, 255)))
    monkeypatch.setattr(backgrounds, "SEASON_ART_DIR", tmp_path)
    return folder


def test_a_run_without_stamp_art_keeps_the_initials_disc(session, season_id) -> None:
    _claimed(session, season_id, free=4)
    stamp = _stamp(session)
    assert stamp.art is None
    assert stamp.gold is True


def test_a_player_without_a_pass_row_has_a_plain_stamp(session, season_id) -> None:
    stamp = _stamp(session)
    assert (stamp.gold, stamp.art) == (False, None)


def test_an_unfinished_free_pass_is_not_gold(session, season_id, art_dir) -> None:
    _claimed(session, season_id, free=3)
    stamp = _stamp(session)
    assert stamp.gold is False
    assert stamp.art == (art_dir / "stamp.png").read_bytes()


def test_the_premium_stamp_tier_shows_the_plus_art(session, season_id, art_dir) -> None:
    _claimed(session, season_id, free=4, premium=4)
    stamp = _stamp(session)
    assert stamp.gold is True
    assert stamp.art == (art_dir / "stamp_plus.png").read_bytes()


def test_premium_not_yet_at_the_stamp_tier_shows_the_plain_art(session, season_id, art_dir) -> None:
    _claimed(session, season_id, free=4, premium=3)
    assert _stamp(session).art == (art_dir / "stamp.png").read_bytes()


def test_season_stamp_reads_the_right_file(art_dir) -> None:
    assert backgrounds.season_stamp("demo_1", plus=False) == (art_dir / "stamp.png").read_bytes()
    assert (
        backgrounds.season_stamp("demo_1", plus=True) == (art_dir / "stamp_plus.png").read_bytes()
    )
    assert backgrounds.season_stamp("nope", plus=True) is None


def test_a_run_without_a_stamp_tier_is_never_gold(
    session, season_id, art_dir, fake_runs, monkeypatch
) -> None:
    demo = fake_runs["demo_1"]
    runs = {**fake_runs, "demo_1": replace(demo, tiers=demo.tiers[:-1])}
    monkeypatch.setattr(registry, "all_runs", lambda: runs)
    _claimed(session, season_id, free=3, premium=3)
    stamp = _stamp(session)
    assert stamp.gold is False
    assert stamp.art == (art_dir / "stamp.png").read_bytes()


def test_an_unregistered_run_gets_a_plain_stamp(session, season_id, monkeypatch) -> None:
    monkeypatch.setattr(registry, "all_runs", dict)
    _claimed(session, season_id, free=4, premium=4)
    stamp = _stamp(session)
    assert (stamp.run_id, stamp.label, stamp.gold, stamp.art) == ("demo_1", "demo_1", False, None)


def test_the_plus_art_falls_back_to_the_stamp_art(session, season_id, art_dir) -> None:
    (art_dir / "stamp_plus.png").unlink()
    _claimed(session, season_id, free=4, premium=4)
    stamp = _stamp(session)
    assert stamp.gold is True
    assert stamp.art == (art_dir / "stamp.png").read_bytes()
