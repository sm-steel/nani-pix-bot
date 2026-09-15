"""Turn a `qlty smells` finding into a failing exit code.

`qlty smells` always exits 0, even when it prints real findings (verified
against the installed CLI, no flag changes this) — so `pre-commit`, which
decides pass/fail purely on exit code, can never fail this check. `--quiet`
makes the emptiness of its output the signal instead: a clean tree prints
nothing, a tree with findings prints only the results (no progress noise).
"""

import shutil
import subprocess
import sys

_QLTY_SMELLS_ARGS = ["smells", "--all", "--no-snippets", "--quiet"]


def _run_qlty() -> subprocess.CompletedProcess[str]:
    qlty = shutil.which("qlty")
    if qlty is None:
        return subprocess.CompletedProcess(
            args=["qlty"],
            returncode=1,
            stdout="",
            stderr="qlty not found on PATH — see CLAUDE.md's Tooling section.",
        )
    return subprocess.run(  # noqa: S603 - argv is a fixed list, no shell, no user input
        [qlty, *_QLTY_SMELLS_ARGS], capture_output=True, text=True
    )


def main() -> int:
    result = _run_qlty()
    output = (result.stdout + result.stderr).strip()
    if output:
        print(output)
    return 1 if (output or result.returncode != 0) else 0


if __name__ == "__main__":
    sys.exit(main())
