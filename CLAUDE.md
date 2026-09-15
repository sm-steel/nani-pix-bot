# nani-pix-bot

Telegram bot for an anime-screenshot guessing game, played in one topic of a
group chat. A player either DMs the bot a screenshot directly, or sends
`/newgame` and picks a real screenshot from Shikimori/Jikan/TMDB instead —
either way, they identify the anime (see `MECHANICS.md` for exactly how —
there are five identification methods) and the bot posts the screenshot
heavily pixelated into the group's game topic, where players guess with
`/guess`, watching the image get progressively clearer as wrong guesses
accumulate — each of the five stages has its own admin-configurable
wrong-guess limit (1/1/2/3/3 by default, so it starts unforgiving and
loosens), and the bot's replies count down the remaining guesses against
it — until someone's right or it's revealed unsolved. Runs as a Docker
Compose stack on the `moscow` VPS.

**Read `ARCHITECTURE.md` before making non-trivial changes** — it covers the
system design, data model, infra topology (why Telegram traffic is proxied
through `amsterdam`, etc.), and the full directory/module layout
("Where things are"). `MECHANICS.md` covers the game rules themselves
(pixelation stages, guess matching, turns, timeout, leaderboard) — read it
before touching anything in `services/game/`, `services/matching.py`,
`services/pixelate.py`, or `services/settings/stage_config.py`.
This file covers how to work in this repo day to day — tooling, testing,
logging, and coding conventions — not where things live; that's
`ARCHITECTURE.md`'s job, so it isn't duplicated here.

## Language / i18n

Bot-facing text (both languages the bot currently ships, RU/EN) lives in
`src/nani_pix_bot/locales/en.json` and `locales/ru.json` — flat
`"namespace.key": "template with {placeholders}"` maps, loaded and looked
up by `services/i18n.py`'s `t(key: str, lang: str, **kwargs) -> str`.
Every command handler that sends a reply calls `settings.get_language(session)`
first (cheap — `BotSettings` is a singleton row) and passes that `lang`
into every `t()`/keyboard-builder call.

This is a **deliberately simple custom dict/JSON approach**, not
`gettext`/`Babel`/the `python-i18n` package — two languages and a bot's
worth of strings don't need that ceremony (see this project's KISS
convention below). A key missing in `ru.json` falls back to `en.json`
(logged as a `WARNING`, so gaps get noticed); a key missing even there
logs an `ERROR` and returns the bare key rather than raising — a bad
translation shouldn't crash a live bot.

Two categories of text are **deliberately not translated**: third-party
brand names (`"AniList"`/`"Shikimori"`/`"Jikan"`/`"TMDB"` — in the
method-picker keyboard and everywhere else via `Provider.display_name`
in `models/enums.py`, the single source of truth for them) and the
`/language` picker's
own native-name labels (`"🇷🇺 Русский"`/`"🇬🇧 English"` — a language
switcher inherently shows each option in its own name, so translating
through the *currently selected* language would be circular).

Only a group's admin/owner may change the language (`/language`, DM
only), checked live via `commands/helpers/membership.py::is_group_admin`
— the same Telegram `get_chat_member` call `is_group_member` already
makes for the DM game-setup gate.

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
rather than a second copy of one they already do. **These are two separate
subcommands, invoked separately, not one check that covers both**: `qlty
smells` never runs plugins (trufflehog included) regardless of what's
enabled in `.qlty/qlty.toml` — only `qlty check` does.

```sh
qlty smells --all --no-snippets           # complexity + duplication findings
qlty check --filter trufflehog --all      # secret scan
qlty metrics --all --sort complexity --limit 15   # per-file complexity/LOC table
```

**The secret scan only catches *verified* secrets by default** — qlty's
bundled trufflehog driver hardcodes `--only-verified`
(`trufflehog filesystem --json --fail --only-verified --no-update`), and
there is no documented `.qlty/qlty.toml` setting to change that without
editing trufflehog's own plugin definition (out of scope, and it
wouldn't survive a fresh `qlty` install anyway since it's not something
this repo commits). In practice this means the check only fires on a
secret trufflehog can actually confirm is live against the issuing
service's API (or, for private keys, a known-compromised-key match) —
not on any secret-shaped string. A plausible-looking but fake or already-
revoked credential will **not** trip it; a real, still-valid one will.
This narrows the safety net considerably versus what "secret scanning is
enabled" might suggest — worth knowing before relying on it as the sole
backstop for something sensitive.

**Never resolve a qlty finding (or a ruff/ty finding) by loosening its
check (raising a threshold, disabling a rule, excluding a path) — fix the
actual code.** A finding is a real signal about the code, not the config;
adjusting `.qlty/qlty.toml`/`pyproject.toml`'s tool sections to make it stop
appearing is hiding the problem, not solving it. If a finding turns out to
be a false positive on inspection (not just inconvenient), say so
explicitly and get confirmation before touching the config — don't default
to loosening it.

**All five checks — `ruff check`, `ruff format --check`, `ty check`, `qlty
smells` (complexity + duplication), `qlty check --filter trufflehog`
(secret scan) — run as a git pre-commit hook** via
[pre-commit](https://pre-commit.com) (`.pre-commit-config.yaml`, installed
as a `uv` dev dependency — `uv run pre-commit install` sets up the hook
once per clone). `qlty smells` itself always exits 0 regardless of
findings, so its hook entry is `scripts/qlty_smells_gate.py`, a small
wrapper that turns a non-empty `--quiet` result into a failing exit code;
`qlty check` doesn't have that problem — verified directly, it already
exits non-zero on a real finding by default (`--fail-level` defaults to
`fmt`, not gated behind an opt-in flag), so its hook entry shells out to
it directly, no wrapper needed. A commit is blocked if any check fails.
Every hook is a `local` entry (`language: system`) that
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

**This table sat unapplied for two full rounds of feature work (v1 and
most of v2)** — by the time issue #23 swept the codebase for it, only 3
files logged anything at all, and every exception-swallowing branch
(`except _SEARCH_SERVICE_ERRORS`, `except Forbidden`, the DB
rollback path) had zero trace of what actually went wrong. That gap was
a real cost, not a hypothetical one: diagnosing a live-group issue meant
guessing. **Every new command handler, service function, or job
callback gets its logging added in the same commit that adds the
behavior — not queued for a later sweep.** If a change touches a
branch with no log call yet (especially a `try`/`except` around a
network call, or a background `JobQueue` callback with no synchronous
caller to notice a silent failure), add one while you're there.

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

The first three (not `pytest`) plus `qlty smells` and `qlty check --filter
trufflehog` also run automatically as a git pre-commit hook (see Tooling
above) — committing re-verifies them regardless, but running them yourself
first means the commit doesn't just fail on the first attempt.

## Test-driven development

Every unit in `services/` and `models/` gets a failing test written first,
then the minimal implementation to make it pass, then refactor. This
matters more here than in most bots: `services/matching.py`'s fuzzy-match
threshold and `services/pixelate.py`'s width-scaling math are exactly the
kind of logic that's easy to eyeball as "probably right" and quietly wrong
at the edges — write the edge-case test (near-miss title, empty guess,
already-at-the-final-stage exhaustion) before the implementation, not
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
- **DRY** the game constants (the per-stage wrong-guess thresholds, the
  fuzzy-match threshold, the timeout duration) — define them once as named
  constants in `services/`, not re-literaled across handlers and tests.
- Prefer pure functions for anything with game logic in it (guess
  normalization/matching, pixelation math, stage-advance decisions) —
  deterministic given their inputs, so they're easy to unit-test with `uv
  run pytest` (write the test first).
