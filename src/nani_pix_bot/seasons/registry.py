"""Finds the run modules (seasons spec §1)."""

import importlib
import pkgutil
from collections.abc import Mapping
from functools import cache
from types import MappingProxyType

from nani_pix_bot.seasons.definition import SeasonRun

RUNS_PACKAGE = "nani_pix_bot.seasons.runs"


class RunModuleError(RuntimeError):
    """A run module that doesn't declare a usable RUN."""


def discover(package: str) -> dict[str, SeasonRun]:
    root = importlib.import_module(package)
    runs: dict[str, SeasonRun] = {}
    for info in sorted(pkgutil.iter_modules(root.__path__), key=lambda i: i.name):
        module = importlib.import_module(f"{package}.{info.name}")
        run = getattr(module, "RUN", None)
        if not isinstance(run, SeasonRun):
            msg = f"{module.__name__} has no RUN = SeasonRun(...)"
            raise RunModuleError(msg)
        if run.run_id != info.name:
            msg = f"{module.__name__}: run_id {run.run_id!r} must equal the module name"
            raise RunModuleError(msg)
        runs[run.run_id] = run
    return runs


@cache
def all_runs() -> Mapping[str, SeasonRun]:
    return MappingProxyType(discover(RUNS_PACKAGE))


def get(run_id: str) -> SeasonRun | None:
    return all_runs().get(run_id)
