# Suggested Commands

Dev machine is **Windows**; this session's shell tools are Git Bash
(POSIX-ish) and PowerShell — prefer the Bash tool's POSIX syntax
(`/dev/null`, forward slashes, `$VAR`) unless a command is
Windows-specific.

## Core loop
```sh
uv sync                                   # install deps + create .venv
uv run pytest                             # full test suite
uv run pytest tests/path/to/test_x.py     # targeted
uv run ruff check .                       # lint (--fix for autofix)
uv run ruff format .                      # format
uv run ty check                           # type check
```

## qlty (separate native binary, not uv-managed)
```sh
qlty smells --all --no-snippets           # complexity + duplication
qlty check --filter trufflehog --all      # secret scan (different subcommand!)
qlty metrics --all --sort complexity --limit 15   # per-file complexity/LOC
```

## Pre-commit
```sh
uv run pre-commit install                 # one-time per clone
uv run pre-commit run --all-files         # same checks CI runs
git commit --no-verify                    # emergency skip only — not habitual
```

## GitHub (task tracking is Issues, not a separate file)
```sh
gh issue list / create / close
gh pr create / merge --merge --delete-branch   # this repo uses merge commits, not squash
gh api repos/sm-steel/nani-pix-bot/milestones
```

## Windows-specific gotchas hit in practice
- A worktree-isolated Bash session refuses multi-line/compound commands
  it can't statically verify stay inside the worktree (e.g. shell
  loops, `cd ... && other-worktree-path`) — use single plain commands
  per worktree instead of looping across worktrees.
- `git worktree remove` can fail with "Permission denied" / "Device or
  resource busy" if a process (e.g. a lingering `qlty` run) still holds
  a file handle in the worktree — git still unregisters it from
  `git worktree list`; the leftover directory can be deleted later once
  unlocked, it's not blocking.
- A shell opened before `qlty` was installed won't see it on `PATH`
  until restarted.
