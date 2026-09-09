"""Smoke test for the package/entry-point scaffolding.

Real behavioral tests land alongside each feature per CLAUDE.md's TDD
section — this just confirms the package is importable and wired up as
the ``nani-pix-bot`` console script expects.
"""

import pytest

from nani_pix_bot.app import main


def test_main_is_not_yet_implemented() -> None:
    """Placeholder until app wiring lands — replace once app.main() does real work."""
    with pytest.raises(NotImplementedError):
        main()
