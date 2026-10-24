"""Fake costume art, so the costume tests never depend on the real assets."""

from pathlib import Path

import pytest
from PIL import Image

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
