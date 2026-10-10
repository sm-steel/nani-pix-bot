"""Every bot command appears in the guide's command reference (issue #361)."""

import ast
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
APP = ROOT / "src" / "nani_pix_bot" / "app.py"
TABLE = ROOT / "docs" / "site" / "src" / "components" / "CommandTable.astro"


def _registered(source: str) -> set[str]:
    """Command names from every `CommandHandler(...)` call, however it's
    wrapped, including the list/tuple form."""
    names: set[str] = set()
    for node in ast.walk(ast.parse(source)):
        if not (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id == "CommandHandler"
            and node.args
        ):
            continue
        first = node.args[0]
        items = first.elts if isinstance(first, (ast.List, ast.Tuple)) else [first]
        names |= {
            i.value for i in items if isinstance(i, ast.Constant) and isinstance(i.value, str)
        }
    return names


def _documented() -> set[str]:
    """Command names in CommandTable's GROUPS map: `['name', '/syntax']` rows."""
    return set(re.findall(r"\['(\w+)', '/", TABLE.read_text(encoding="utf-8")))


def test_finds_registrations_ruff_wrapped_or_listed() -> None:
    source = """
app.add_handler(CommandHandler("guess", guess_command))
app.add_handler(
    CommandHandler(
        "setgamesenabled", gamesenabled.setgamesenabled_command
    )
)
app.add_handler(CommandHandler(["start", "help"], onboarding.start_command))
"""
    assert _registered(source) == {"guess", "setgamesenabled", "start", "help"}


def test_every_registered_command_is_in_the_command_table() -> None:
    registered = _registered(APP.read_text(encoding="utf-8"))
    assert registered, "no CommandHandler registrations found — has app.py moved?"
    assert sorted(registered - _documented()) == []


def test_every_documented_command_is_still_registered() -> None:
    registered = _registered(APP.read_text(encoding="utf-8"))
    assert sorted(_documented() - registered) == []


MENU = ROOT / "src" / "nani_pix_bot" / "commands" / "helpers" / "bot_menu.py"


def _menu(list_name: str) -> set[str]:
    source = MENU.read_text(encoding="utf-8")
    block = re.search(rf"{list_name} = \[(.*?)\n    \]", source, re.DOTALL)
    assert block, f"{list_name} not found in bot_menu.py"
    return set(re.findall(r'BotCommand\("(\w+)"', block.group(1)))


def _table_groups() -> dict[str, set[str]]:
    source = TABLE.read_text(encoding="utf-8")
    return {
        group: set(re.findall(r"\['(\w+)', '/", body))
        for group, body in re.findall(r"\n  (\w+): \[(.*?)\n  \],", source, re.DOTALL)
    }


def test_each_command_is_listed_where_telegrams_menu_offers_it() -> None:
    private, group = _menu("private_commands"), _menu("group_commands")
    where = {"both": private & group, "topic": group - private}
    where["dm"] = where["admin"] = private - group
    groups = _table_groups()
    assert set(groups) == {"topic", "dm", "both", "admin"}
    misplaced = {
        name: table_group
        for table_group, names in groups.items()
        for name in names
        if name not in where[table_group]
    }
    assert misplaced == {}
