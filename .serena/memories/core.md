# nani-pix-bot — Core

Telegram bot: anime-screenshot guessing game, one topic in one group chat.
See `CLAUDE.md` (day-to-day workflow/conventions), `ARCHITECTURE.md`
(system design + full "Where things are" module tree), `MECHANICS.md`
(game rules) at repo root — these are the canonical, actively-maintained
docs; do not duplicate their content here, only what a future agent needs
that isn't obvious from reading them.

## Source map — do not re-derive, read this instead

`ARCHITECTURE.md`'s "Where things are" section is the authoritative full
directory/module tree. Each `commands/`/`services/` subpackage's own
`__init__.py` docstring (`dm_start/`, `game_flow/`, `services/game/`,
`services/search/`, `services/settings/`) is the more detailed, load-
bearing version — **if the tree and a docstring ever disagree, trust the
docstring and fix the tree, not the reverse.**

Layer boundary (enforced, not just convention):
`commands/` (Telegram-aware, thin) → `services/` (game logic,
framework-agnostic) → `models/` (SQLAlchemy ORM only). `services/` and
`models/` never import `python-telegram-bot`. `jobs/` is Telegram-aware
like `commands/` but its entries are JobQueue callbacks, not handlers —
a sibling package, not nested under `commands/`.

## Non-obvious invariants

- **DB-derived flow state, not in-memory.** DM game-setup state
  (`Game.source`, `Game.setup_step`, `screenshot_source` vs
  `screenshot_picker_provider`) is persisted on the `games` row, never in
  PTB's `user_data` — a redeploy mid-setup must resolve correctly from
  the DB alone. A picked search result's title/synonyms are always
  re-fetched fresh by id (`*.get_by_id`) on pick, never trusted from the
  earlier search-list cache.
- **Only one `SETUP`/`ACTIVE` game at a time** — enforced in
  `services/game/state.py`, not a DB constraint.
- **`screenshot_source` vs `screenshot_picker_provider`** on `Game` are
  deliberately separate columns with different meanings (image
  provenance vs. picker-resolution state) — see `ARCHITECTURE.md`'s data
  model table for the full history of why merging them was a bug.
- Provider search modules (`services/search/*.py`) are called once per
  game at setup time only, never on the `/guess` hot path.
- This is a public GitHub repo — see `CLAUDE.md`'s security rule: never
  put real hostnames/credentials/tokens in an issue/PR/commit, use
  placeholders and point at "the ops vault."

Windows dev machine — see `mem:suggested_commands` for platform-specific
command notes. Tooling/build/test commands: `mem:tech_stack`,
`mem:suggested_commands`. Code style: `mem:conventions`. Definition of
done for a change: `mem:task_completion`.
