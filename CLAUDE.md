# nani-pix-bot

Telegram bot for an anime-screenshot guessing game, played in one topic of a
group chat. A player DMs the bot a screenshot and picks the anime (via an
AniList search); the bot posts it heavily pixelated into the group's game
topic, and players guess with `/guess`, watching the image get progressively
clearer every 5 wrong guesses until someone's right or it's revealed unsolved.
Runs as a Docker Compose stack on the `moscow` VPS.

**Read `ARCHITECTURE.md` before making non-trivial changes** — it covers the
system design, data model, and infra topology (why Telegram traffic is
proxied through `amsterdam`, etc.). `MECHANICS.md` covers the game rules
themselves (pixelation stages, guess matching, turns, timeout, leaderboard) —
read it before touching anything in `services/game.py`,
`services/matching.py`, or `services/pixelate.py`. This file is about where
things live and how to work in this repo day to day.

## Where things are

```
src/nani_pix_bot/
  config.py       # env/.env loading — the only place that reads os.environ
  db.py           # SQLAlchemy engine/session factory, session_scope()
  logging_config.py  # loguru setup, redirects PTB's stdlib logging into it
  app.py          # ApplicationBuilder wiring, handler registration, and
                   # re-arming any pending 2-day timeout JobQueue jobs from
                   # the DB on startup (see MECHANICS.md's Timeout section)
  commands/       # one module per Telegram command (thin: parse update,
                   # call a service, format a reply — no game rules here)
    dm_start.py   # private-chat photo intake + AniList search/pick flow
    guess.py      # /guess — the only handler most wrong-guess traffic hits
    correct.py    # /correct @user — author override
    skip.py       # /skip [@user] — turn handoff when no game is running
    leaderboard.py  # /leaderboard
    timeout.py    # 2-day-timeout JobQueue callback + schedule/cancel/
                   # rearm helpers — not a /command itself, but Telegram
                   # (JobQueue)-aware, so it lives here rather than services/
    helpers/      # shared Telegram-aware plumbing — topic/DM scoping
                   # checks (scoping.py), group-membership checks
                   # (membership.py), inline-keyboard builders (keyboards.py).
                   # Nothing here registers a handler in app.py. Test: does
                   # more than one commands/*.py file need it, or does it
                   # not correspond to an actual /command at all? Either one
                   # means helpers/, not a plain commands/*.py file.
  services/       # the actual game logic — framework-agnostic, no
                   # python-telegram-bot imports in this package
    anilist.py    # AniList GraphQL search (httpx) — called once per game,
                   # at setup time only, never per guess (see MECHANICS.md)
    matching.py   # normalize + rapidfuzz-match a guess against a game's
                   # cached title/synonyms — pure function, fully
                   # deterministic, no network calls
    pixelate.py   # Pillow downscale/upscale pipeline for the 4 stages
    game.py       # the state machine: create/advance/win/unsolved/timeout
                   # transitions — the one place that mutates a Game row
    players.py    # win-count bookkeeping, leaderboard query
  models/         # SQLAlchemy ORM models, one module per table
migrations/       # Alembic migrations
tests/            # mirrors src/ layout
scripts/          # one-off / operational scripts, if any turn out to be needed
Dockerfile, docker-compose.yml   # bot + mariadb, see ARCHITECTURE.md
```

## Tooling

Python 3.11+, managed with `uv`. Don't use pip/venv/poetry directly, and
don't invoke `ruff`/`ty`/`pytest` as bare commands — always run them through
`uv run` so they use the project's pinned versions and `.venv`, not
whatever (if anything) is on PATH.

```sh
uv sync                  # install deps + create .venv
uv run pytest            # test
uv run ruff check .      # lint
uv run ruff format .     # format
uv run ty check          # type check
```

Deployment is Docker-only (see `README.md`) — no bare-metal installs on the
target VPS, including the database.

**[qlty](https://github.com/qltysh/qlty) checks Python complexity and
duplication** — neither is covered by `ruff`/`ty`. It's a standalone native
binary (installed once per machine via `qlty.sh`'s install script,
`~/.qlty/bin` — not a `uv` dependency, nothing in `pyproject.toml`),
configured by the committed `.qlty/qlty.toml`. Only its own built-in
complexity/duplication analysis, plus its `trufflehog` secret-scanning
plugin, are enabled; every other third-party linter plugin it can also run
(ruff, bandit, radarlint, hadolint, ripgrep) is explicitly disabled —
`ruff`/`ty` via `uv run` stay the only linter/type-checker, and `qlty` adds
capabilities they don't have (complexity/duplication, secret scanning)
rather than a second copy of one they already do.

```sh
qlty smells --all --no-snippets   # complexity + duplication findings
qlty metrics --all --sort complexity --limit 15   # per-file complexity/LOC table
```

**Never resolve a qlty finding (or a ruff/ty finding) by loosening its
check (raising a threshold, disabling a rule, excluding a path) — fix the
actual code.** A finding is a real signal about the code, not the config;
adjusting `.qlty/qlty.toml`/`pyproject.toml`'s tool sections to make it stop
appearing is hiding the problem, not solving it. If a finding turns out to
be a false positive on inspection (not just inconvenient), say so
explicitly and get confirmation before touching the config — don't default
to loosening it.

**All four checks — `ruff check`, `ruff format --check`, `ty check`, `qlty
smells` (which includes the `trufflehog` secret scan) — run as a git
pre-commit hook** via [pre-commit](https://pre-commit.com)
(`.pre-commit-config.yaml`, installed as a `uv` dev dependency — `uv run
pre-commit install` sets up the hook once per clone). A commit is blocked if
any of them fail. Every hook is a `local` entry (`language: system`) that
shells out to the project's own `uv run ruff`/`uv run ty` — deliberately
**not** the hosted `astral-sh/ruff-pre-commit` repo, which pins its own
separate tool version independent of this project's `uv.lock` and could
drift out of sync. `qlty` is expected on `PATH`; a shell opened before qlty
was installed won't see it until restarted.

To skip in a genuine emergency: `git commit --no-verify` — but fix what it
would have caught before the next real commit, don't make a habit of it.

**CI (`.github/workflows/`) runs the exact same checks as the local
pre-commit hook — never a separate copy of them.** Three workflows,
GitHub-hosted runners only (never self-hosted — GitHub explicitly warns
against self-hosted runners on public repos, since any fork PR can run
arbitrary code on one, including reading secrets):
- **`checks.yml`** (badge in `README.md`) — installs `uv`+`qlty`, then `uv
  run pre-commit run --all-files` against the committed
  `.pre-commit-config.yaml` — the same hooks the local git hook runs.
  Deliberately not the `pre-commit/action` marketplace action: it does its
  own `pip install pre-commit` into whatever venv is active, but a
  `uv`-managed venv has no `pip` in it — `pre-commit` is already a `uv` dev
  dependency here.
- **`tests.yml`** (badge in `README.md`) — `uv run pytest -q`. No service
  containers: every test fixture uses an in-memory SQLite engine.
- **`deploy.yml`** — only on a push to the default branch (never
  `pull_request`, so a fork PR can never reach its secrets). Re-runs both
  of the above as a `verify` job, then a `deploy` job SSHs into `moscow`
  (via `webfactory/ssh-agent` + a dedicated `MOSCOW_SSH_KEY` deploy key —
  **not** the personal key used to administer `moscow` interactively) and
  runs `git pull --ff-only`, builds, brings up `mariadb`, runs the
  migration via a throwaway `docker compose run --rm` container, then
  `docker compose up -d` for everything — migrating before `bot` starts,
  not after, since `bot`'s startup queries the `games` table (to re-arm
  pending timeouts) and would otherwise crash-loop against a schema a
  pending migration hasn't created yet. Secrets
  (`MOSCOW_SSH_KEY`/`MOSCOW_HOST`/`MOSCOW_USER`) live in the repo's GitHub
  Settings, never in a committed file — see the vault's infrastructure docs
  for what "moscow"/"amsterdam" actually are.

If a local pre-commit pass ever disagrees with `checks.yml`'s result on the
same commit, that's a bug in the CI setup worth fixing directly, not
something to route around by re-running or ignoring.

## Logging

Uses **loguru** (`from loguru import logger`) everywhere, not stdlib
`logging` directly — set up once in `logging_config.py`, which also
redirects python-telegram-bot's own stdlib logging into the same sink (the
standard `InterceptHandler` recipe). Sink level defaults to `INFO`,
overridable via the `LOG_LEVEL` env var (`.env`).

**Log generously, but pick the right level:**

| Level | Use for | Example in this codebase |
|---|---|---|
| `DEBUG` | Routine/internal detail, expected outcomes | a wrong `/guess`'s normalized text and match score, stage-threshold not yet reached |
| `INFO` | A meaningful game event | a game created, a stage advancing, a win, a game going unsolved, a `/skip` |
| `WARNING` | Recoverable anomaly, rejected action | `/guess` outside the game topic, `/skip` from someone who isn't the designated starter, AniList rate-limit retry |
| `ERROR` | Something is actually broken | AniList search failing after retries, a stage image failing to send |

When adding a new log call, ask "would this be useful in production at
`LOG_LEVEL=INFO`, or is it something I'd only want while debugging?" — the
former is `INFO`+, the latter is `DEBUG`. Don't log routine, frequent,
expected-outcome events at `INFO` — that's what turns `INFO` logs into
background noise nobody reads.

## Verifying changes

**Before considering any Python change done, run all three — in this
order, via `uv run` — and fix everything they report:**

```sh
uv run ruff check .      # lint (add --fix to autofix what's safe to autofix)
uv run ruff format .     # format
uv run ty check          # type check
```

Then run the relevant tests (`uv run pytest`, or a narrower `uv run pytest
tests/path/to/test_thing.py` while iterating). A change isn't finished if
any of the four fail — don't leave known ruff/ty findings for later or
describe work as complete while they're still red.

The first three (not `pytest`) plus `qlty smells` also run automatically as
a git pre-commit hook (see Tooling above) — committing re-verifies them
regardless, but running them yourself first means the commit doesn't just
fail on the first attempt.

## Test-driven development

Every unit in `services/` and `models/` gets a failing test written first,
then the minimal implementation to make it pass, then refactor. This
matters more here than in most bots: `services/matching.py`'s fuzzy-match
threshold and `services/pixelate.py`'s stage dimensions are exactly the
kind of logic that's easy to eyeball as "probably right" and quietly wrong
at the edges — write the edge-case test (near-miss title, empty guess,
already-pixelated-to-x2 stage exhaustion) before the implementation, not
after.

## Task tracking (GitHub Issues)

Implementation progress is tracked as **GitHub Issues** on this repo
(`sm-steel/nani-pix-bot`, public), grouped into a **Milestone** (e.g.
"v1"). Use the `gh` CLI (`gh issue list`, `gh issue create`, `gh issue
close`, `gh api repos/sm-steel/nani-pix-bot/milestones`) rather than
inventing a separate tracking file — the issue tracker is the source of
truth for what's done/in progress/planned.

**Security rule — no exceptions, the repo is public:**

> **Never put real logins, hostnames, IPs, passwords, API keys/tokens, SSH
> keys, or any other credential into an issue title, issue body, issue
> comment, PR description, PR comment, or commit message.** This includes
> the owned VPS infrastructure this bot deploys to. Use the same
> placeholders as the rest of this repo (`USERNAME`, `PASSWORD`,
> `PROXY_HOST`, `PROXY_PORT`, `<user>`, `<pass>`, or an alias like `moscow`/
> `amsterdam` with no FQDN) and point at "the ops vault" for real values —
> never write them out, even "temporarily" or "just to explain the bug."
> Everything in this repo — commits, issues, PRs, history — is public and
> indexed by anyone/anything crawling GitHub; there is no private fallback
> to catch a slip.

## Coding practices

- **KISS.** This is a small social game for one group chat, not a
  multi-tenant platform. Prefer the boring, direct implementation over a
  general one — one active game at a time, one configured group+topic, no
  configurability nothing currently needs.
- **YAGNI.** Don't build multi-group support, a web admin panel, or
  combat/story-style features — none were asked for. If a future need
  shows up, it gets its own design pass then.
- **SOLID, applied pragmatically:**
  - *Single responsibility*: a `commands/` handler parses the Telegram
    update and formats the reply; a `services/` function holds the actual
    rule (matching, pixelation, stage transitions). Keep that boundary —
    it's what makes the game logic testable without spinning up a bot.
  - *Dependency inversion*: `services/` and `models/` never import
    `telegram`/`python-telegram-bot`. Command handlers depend on services,
    not the other way around.
  - Don't chase the rest of SOLID for its own sake — no interfaces with a
    single implementation, no factories for things that are never swapped.
- **DRY** the game constants (wrong-guesses-per-stage, the fuzzy-match
  threshold, the timeout duration) — define them once as named constants in
  `services/`, not re-literaled across handlers and tests.
- Prefer pure functions for anything with game logic in it (guess
  normalization/matching, pixelation math, stage-advance decisions) —
  deterministic given their inputs, so they're easy to unit-test with `uv
  run pytest` (write the test first).
