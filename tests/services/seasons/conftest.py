import pytest


@pytest.fixture(autouse=True)
def _season_runs(fake_runs):
    """The shared fake runs, applied to every test in this folder."""
