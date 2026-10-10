"""Every bot command appears in the guide's command reference (issue #361)."""

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
APP = ROOT / "src" / "nani_pix_bot" / "app.py"
TABLE = ROOT / "docs" / "site" / "src" / "components" / "CommandTable.astro"


def test_every_registered_command_is_in_the_command_table() -> None:
    registered = set(re.findall(r'CommandHandler\("(\w+)"', APP.read_text(encoding="utf-8")))
    documented = set(re.findall(r"\['(\w+)',", TABLE.read_text(encoding="utf-8")))
    assert registered, "no CommandHandler registrations found — has app.py moved?"
    assert sorted(registered - documented) == []
