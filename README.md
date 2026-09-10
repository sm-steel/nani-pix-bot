# nani-pix-bot

![Checks](https://github.com/sm-steel/nani-pix-bot/actions/workflows/checks.yml/badge.svg)
![Tests](https://github.com/sm-steel/nani-pix-bot/actions/workflows/tests.yml/badge.svg)

Telegram bot for an anime-screenshot guessing game, played in one topic of a
group chat. Someone DMs the bot a screenshot and identifies the anime (see
`MECHANICS.md` for exactly how); the bot posts it heavily pixelated into
the group's game topic, and it gets progressively clearer every 5 wrong
`/guess` attempts until someone's right or it's revealed unsolved. Runs on
`moscow`, routing Telegram API traffic through `amsterdam`'s proxy (moscow
has no direct route to `api.telegram.org`).

See `MECHANICS.md` for the full rules and `ARCHITECTURE.md` for the system
design.

## Stack

- Python 3.11+, managed with [uv](https://docs.astral.sh/uv/)
- [python-telegram-bot](https://docs.python-telegram-bot.org/) (async, long-polling)
- SQLAlchemy + Alembic against MariaDB
- Pillow (pixelation), rapidfuzz (guess matching), httpx (AniList GraphQL +
  Shikimori REST)
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

## Deployment

Runs entirely in Docker (bot + MariaDB), via `docker-compose.yml`. On the
target host (moscow):

```sh
cp .env.example .env     # fill in real secrets — see comments in the file
docker compose build
docker compose up -d mariadb
docker compose run --rm --no-deps bot uv run --no-dev alembic upgrade head
docker compose up -d
docker compose logs -f bot
```

Migrate before `bot` starts, not after — see `CLAUDE.md`'s CI/CD section
for why (its startup queries the database immediately, and a crash-looping
container can't be fixed by exec-ing into it).

The `mariadb` service owns its data in a named volume (`mariadb_data`); the
bot connects to it over the compose network as `mariadb:3306`, not
`localhost`.

## Continuous Integration

Two GitHub Actions workflows run on every push/PR — **Checks**
(`.github/workflows/checks.yml`: `ruff`, `ty`, and `qlty smells`, via the
same `.pre-commit-config.yaml` the local pre-commit hook uses) and **Tests**
(`.github/workflows/tests.yml`: `pytest`). A third workflow, **Deploy**
(`.github/workflows/deploy.yml`), runs only on a push to the default
branch: it re-verifies both of the above, then SSHs into `moscow` and
rebuilds/restarts the bot stack — merging is what ships a change, no manual
deploy step. See `CLAUDE.md` for details.
