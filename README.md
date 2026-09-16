# nani-pix-bot

![nani-pix-bot](docs/social-preview.png)

![Checks](https://github.com/sm-steel/nani-pix-bot/actions/workflows/checks.yml/badge.svg)
![Tests](https://github.com/sm-steel/nani-pix-bot/actions/workflows/tests.yml/badge.svg)

Telegram bot for an anime-screenshot guessing game, played in one topic of a
group chat. Someone DMs the bot a screenshot — or sends `/newgame` and picks
a real screenshot from Shikimori/Jikan/TMDB instead — and identifies the
anime (see `MECHANICS.md` for exactly how); the bot posts it heavily
pixelated into the group's game topic, and it gets progressively clearer
as wrong `/guess` attempts accumulate — each of the five stages has its
own admin-configurable wrong-guess limit (1/1/2/3/3 by default) — until
someone's right or it's revealed unsolved.

See `MECHANICS.md` for the full rules and `ARCHITECTURE.md` for the system
design. Want to run your own instance? See **Self-hosting** below.

## Stack

- Python 3.11+, managed with [uv](https://docs.astral.sh/uv/)
- [python-telegram-bot](https://docs.python-telegram-bot.org/) (async, long-polling)
- SQLAlchemy + Alembic against MariaDB
- Pillow (pixelation), rapidfuzz (guess matching), httpx (AniList/Shikimori
  GraphQL, Jikan/TMDB REST)
- Linting/formatting: `ruff`. Type checking: `ty`. Complexity/duplication/secrets: `qlty`.

## Dev setup

```sh
uv sync                  # install deps + create .venv
cp .env.example .env     # fill in BOT_TOKEN, DATABASE_URL, etc.
uv run pytest            # run tests
uv run ruff check .      # lint
uv run ruff format .     # format
uv run ty check          # type check
```

## Self-hosting

Runs entirely in Docker (bot + MariaDB), via `docker-compose.yml`. This
section walks through setting up your own instance from nothing.

### Prerequisites

- Docker and the Docker Compose plugin (`docker compose version` should work).
- A Telegram account.

### 1. Create a Telegram bot

1. Open a chat with [@BotFather](https://t.me/BotFather) and send `/newbot`.
2. Follow the prompts to pick a name and a `...bot`-suffixed username.
3. BotFather replies with a token, e.g. `123456789:AAExampleTokenTextHere` —
   this is your `BOT_TOKEN`. Keep it secret; anyone with it can control
   your bot.

### 2. Set up your group and topic

1. Create a Telegram group (or use an existing one) and turn it into a
   **supergroup with Topics enabled**: Group Settings → Topics → on. This
   is required — the bot posts into one specific topic within the group,
   not the group's main chat.
2. Add your bot to the group, then make it an **admin** with at least the
   **"Pin messages"** permission — the bot pins the current game image as
   it updates, and silently skips pinning (logged, but otherwise harmless)
   without that permission.
3. Create or pick the forum topic the game will be played in.

### 3. Get your `GROUP_CHAT_ID` and `GAME_TOPIC_ID`

Before the bot container is running (so nothing else is consuming
updates), send any **command-form** message — e.g. `/start` — in the
target topic. It's fine that nothing responds yet. Then open this URL in
a browser or `curl` it (with your real token):

```
https://api.telegram.org/bot<BOT_TOKEN>/getUpdates
```

In the JSON response, find that message and read:
- `result[].message.chat.id` — your `GROUP_CHAT_ID` (a negative number
  for supergroups, e.g. `-1001234567890`).
- `result[].message.message_thread_id` — your `GAME_TOPIC_ID`.

(A plain, non-command message may not appear here — Telegram bots only
receive commands, replies to themselves, or @-mentions from group chats
by default, which is why step 3 says to send a command.)

### 4. Configure `.env`

```sh
cp .env.example .env
```

Fill in, at minimum: `BOT_TOKEN`, `GROUP_CHAT_ID`, `GAME_TOPIC_ID`,
`ADMIN_USER_IDS` (comma-separated Telegram user ids allowed to run
admin-gated commands like `/language`/`/setstageconfig`), and the
`MARIADB_*`/`DATABASE_URL` pair (pick your own passwords; just keep the
two in sync — see the file's comments). `TMDB_READ_ACCESS_TOKEN` and
`TELEGRAM_PROXY_URL` are both optional — see the comments in
`.env.example` for what each unlocks and when you'd need it.

### 5. Run it

Build from source:

```sh
docker compose build
docker compose up -d mariadb
docker compose run --rm --no-deps bot uv run --no-dev alembic upgrade head
docker compose up -d
docker compose logs -f bot
```

Or use a pre-built image instead of building locally: in
`docker-compose.yml`, replace the `bot` service's `build: .` with
`image: ghcr.io/sm-steel/nani-pix-bot:X.Y.Z` (see this repo's
[Releases](https://github.com/sm-steel/nani-pix-bot/releases) page for
available tags), then run the same commands minus `docker compose build`.

Either way: **migrate before `bot` starts, not after** — its startup
queries the database immediately, and a crash-looping container can't be
fixed by exec-ing into it. Once it's up, `docker compose logs -f bot`
should show it polling Telegram; DM it or try `/version` in your topic to
confirm it's responding.

The `mariadb` service owns its data in a named volume (`mariadb_data`); the
bot connects to it over the compose network as `mariadb:3306`, not
`localhost`.

### Updating

This repo publishes a versioned image to `ghcr.io/sm-steel/nani-pix-bot`
on every release (semantic-release, driven by Conventional Commits — see
`CLAUDE.md`). How you roll updates out to your own instance is up to
you — `docker compose pull && docker compose up -d`, a small script, a
tool like [Watchtower](https://containrrr.dev/watchtower/), or your own
CI/CD are all reasonable choices. Remember to run any pending Alembic
migration before restarting `bot`, same as initial setup above.

### Health checks and auto-restart

`bot`'s `HEALTHCHECK` watches a heartbeat file (`src/nani_pix_bot/heartbeat.py`)
that's only touched after a real, successful Telegram `getUpdates` cycle — a
stale file means polling is wedged even though the process is still running
(see issue #51). `restart: unless-stopped` in `docker-compose.yml` already
recovers most failures on its own; the `bot` service's `autoheal.*` labels
are there for anyone who separately runs a watcher like
[docker-autoheal](https://github.com/tmknight/docker-autoheal) — otherwise
they're a harmless no-op.

## Continuous Integration

Two GitHub Actions workflows run on every push/PR — **Checks**
(`.github/workflows/checks.yml`: `ruff`, `ty`, and `qlty smells`, via the
same `.pre-commit-config.yaml` the local pre-commit hook uses) and **Tests**
(`.github/workflows/tests.yml`: `pytest`). A third workflow, **Release**
(`.github/workflows/release.yml`), runs only on a push to `master` — this
repo's dedicated release branch, not its default branch (see
`CLAUDE.md`'s "Branching & workflow" section): it cuts a semantic-release
version and, when a release actually happens, builds and pushes a
versioned image to `ghcr.io/sm-steel/nani-pix-bot`. There is no deploy
workflow in this repo —
see **Self-hosting → Updating** above for how to roll a release out to
your own instance. See `CLAUDE.md` for details.
