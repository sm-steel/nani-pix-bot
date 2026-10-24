"""Fake costume art, so the costume tests never depend on the real assets."""

from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from PIL import Image

from nani_pix_bot.models.enums import SeasonStatus
from nani_pix_bot.models.season import SeasonSchedule
from nani_pix_bot.seasons import registry
from nani_pix_bot.services import mascot

ORANGE = (255, 120, 0, 255)


@pytest.fixture
def costume_art(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """Six solid-orange 384x440 poses of the costume "test_hat" in a temp dir that
    stands in for mascot.COSTUME_DIR. Returns the costume key."""
    folder = tmp_path / "costumes" / "test_hat"
    folder.mkdir(parents=True)
    for pose in mascot.Pose:
        Image.new("RGBA", (384, 440), ORANGE).save(folder / f"{pose.value}.png")
    monkeypatch.setattr(mascot, "COSTUME_DIR", tmp_path / "costumes")
    mascot._source.cache_clear()
    mascot.sized.cache_clear()
    yield "test_hat"
    mascot._source.cache_clear()
    mascot.sized.cache_clear()


@pytest.fixture
def costume_run(costume_art, monkeypatch: pytest.MonkeyPatch):
    """The fake run "demo_1" now dresses Nani in the "test_hat" costume by
    default. Pair with `activate_season` to make it the active run."""
    demo = registry.discover("tests.seasons.fake_runs")["demo_1"]
    run = replace(demo, mascot_costume=costume_art)
    monkeypatch.setattr(registry, "all_runs", lambda: {"demo_1": run})
    registry.all_items.cache_clear()
    yield costume_art
    registry.all_items.cache_clear()


def activate_season(session) -> int:
    """An active "demo_1" season row; returns its id."""
    now = datetime.now(UTC)
    row = SeasonSchedule(
        run_id="demo_1",
        start_at=now - timedelta(days=1),
        end_at=now + timedelta(days=9),
        status=SeasonStatus.ACTIVE,
        created_by=9,
    )
    session.add(row)
    session.flush()
    return row.id
