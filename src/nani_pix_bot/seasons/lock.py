"""The frozen-run guard (seasons spec §1, §6): runs.lock records a hash of
every shipped run module; tests/seasons/test_lock.py fails when one changes.
A change to runs.lock itself is a visible line in review — the point.
`uv run python -m nani_pix_bot.seasons.lock` prints the lines to add."""

import hashlib
from pathlib import Path

LOCK_FILE = Path(__file__).with_name("runs.lock")
RUNS_DIR = Path(__file__).with_name("runs")


def module_hash(path: Path) -> str:
    # LF-normalized: a Windows checkout must hash the same as CI's.
    return hashlib.sha256(path.read_bytes().replace(b"\r\n", b"\n")).hexdigest()


def read_lock(path: Path) -> dict[str, str]:
    entries: dict[str, str] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        text = line.strip()
        if text and not text.startswith("#"):
            run_id, digest = text.split()
            entries[run_id] = digest
    return entries


def current(runs_dir: Path) -> dict[str, str]:
    return {p.stem: module_hash(p) for p in sorted(runs_dir.glob("*.py")) if p.stem != "__init__"}


def problems(locked: dict[str, str], current: dict[str, str]) -> list[str]:
    found = []
    for run_id in sorted(locked.keys() | current.keys()):
        if run_id not in current:
            found.append(f"{run_id}: a locked run module is gone")
        elif run_id not in locked:
            found.append(
                f"{run_id}: not in runs.lock — add it (python -m nani_pix_bot.seasons.lock)"
            )
        elif locked[run_id] != current[run_id]:
            found.append(f"{run_id}: a locked run module changed — runs are frozen once shipped")
    return found


def main() -> None:
    for problem in problems(read_lock(LOCK_FILE), current(RUNS_DIR)):
        print(f"# {problem}")
    for run_id, digest in current(RUNS_DIR).items():
        print(f"{run_id} {digest}")


if __name__ == "__main__":
    main()
