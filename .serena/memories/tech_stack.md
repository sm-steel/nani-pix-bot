# Tech Stack

- Python 3.11+, dependency/venv management via **`uv`** exclusively — no
  pip/venv/poetry.
- **python-telegram-bot** (PTB) — bot framework; `ApplicationBuilder`
  wiring in `app.py`. Default handler group `0`; one handler
  (`player_tracking.remember_user`) deliberately runs in group `-1`.
- **SQLAlchemy** ORM, `models/` package. Prod: MariaDB (`mariadb:11`
  Docker image). Tests: `sqlite:///:memory:` — every test fixture uses
  an in-memory engine, no service containers in CI.
- **Alembic** migrations, `migrations/`.
- **httpx** for all outbound provider HTTP (search services, screenshot
  downloads).
- **loguru** for all logging (not stdlib `logging` directly) — set up
  once in `logging_config.py`, which also redirects PTB's stdlib logging
  into the same sink.
- **rapidfuzz** for guess fuzzy-matching (`services/matching.py`).
- **Pillow** for pixelation (`services/pixelate.py`).
- Search providers: AniList + Shikimori (both GraphQL), Jikan + TMDB
  (both REST) — shared plumbing in `services/search/graphql.py` /
  `rest.py` respectively; `parsing.py`'s entry/field guards and
  `http_retry.py`'s 429 retry loop are shared by all four.
- **Test stack:** `pytest` + `pytest-asyncio`.
- **Lint/format/type-check:** `ruff` (lint+format) + `ty` (Astral's type
  checker) — both via `uv run`, never invoked bare.
- **`qlty`** — standalone native binary (not a `uv`/pyproject
  dependency, installed once per machine to `~/.qlty/bin`), config in
  `.qlty/qlty.toml`. Covers complexity/duplication (`qlty smells`) and
  secret scanning via a `trufflehog` plugin (`qlty check --filter
  trufflehog`) — **two separate subcommands**, `smells` never runs
  plugins regardless of `.qlty/qlty.toml`'s config. Every other
  third-party qlty plugin (ruff/bandit/radarlint/hadolint/ripgrep) is
  explicitly disabled — `ruff`/`ty` stay the only linter/type-checker.
- Deployment: Docker Compose only (`bot` + `mariadb` containers), no
  bare-metal installs including the DB. See `ARCHITECTURE.md` for the
  `moscow`/`amsterdam` proxy topology.
