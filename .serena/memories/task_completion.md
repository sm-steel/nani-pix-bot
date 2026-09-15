# Task Completion Checklist

A Python change is not done until all of these are clean, in this order:

```sh
uv run ruff check .      # lint — fix everything, --fix for autofixable
uv run ruff format .     # format
uv run ty check          # type check
uv run pytest            # full suite (or a targeted path while iterating,
                          # but full suite before calling something done)
qlty smells --all --no-snippets        # complexity + duplication
qlty check --filter trufflehog --all   # secret scan
```

All five also run automatically as a git pre-commit hook AND in CI
(`checks.yml` runs `uv run pre-commit run --all-files` — CI is never a
separate copy of the local hooks; if they ever disagree, that's a CI bug
to fix directly). Running them yourself first just means the commit
doesn't fail on the first attempt — it doesn't skip re-verification.

`qlty smells` itself always exits 0 regardless of findings — its
pre-commit hook entry is a wrapper (`scripts/qlty_smells_gate.py`) that
turns a non-empty `--quiet` result into a real failing exit code.
`qlty check` already exits non-zero on a real finding by default
(`--fail-level` defaults to `fmt`), no wrapper needed there.

Never mark a change complete with known ruff/ty/test failures "for
later." Never loosen a check's own config to make a finding disappear —
see `mem:conventions`.
