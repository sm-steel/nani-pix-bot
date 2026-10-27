"""Loads the revealeffect widening migration that carries the Yōkai Night transitions."""

import importlib.util
from pathlib import Path
from types import ModuleType

_PATH = (
    Path(__file__).parents[1]
    / "migrations"
    / "versions"
    / "f2c7a9d4e1b8_widen_revealeffect_enum_yokai.py"
)


def yokai_migration() -> ModuleType:
    spec = importlib.util.spec_from_file_location("f2c7a9d4e1b8_under_test", _PATH)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module
