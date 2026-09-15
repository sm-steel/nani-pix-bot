# Coding Conventions

- **KISS/YAGNI, applied literally, not just stated**: one active game,
  one configured group+topic, no multi-tenancy. Don't build
  configurability/abstraction nothing currently needs — see
  `ARCHITECTURE.md`'s "Roadmap (explicitly out of scope for v1)" before
  assuming a generalization is wanted.
- **SOLID, pragmatic**: single-responsibility split is `commands/`
  (parse+reply) vs `services/` (rules) vs `models/` (persistence) — keep
  that boundary, it's what makes game logic testable without a live bot.
  `services/`/`models/` never import `telegram`. Don't chase interfaces
  with one implementation or factories for things never swapped.
- **DRY the game constants** (per-stage wrong-guess thresholds,
  fuzzy-match threshold, timeout durations) — named constants in
  `services/`, never re-literaled in handlers/tests.
- **Prefer pure functions for game logic** (guess matching, pixelation
  math, stage-advance decisions) — deterministic, easy to unit test.
- **TDD is mandatory for `services/`/`models/`**: failing test → minimal
  implementation → refactor. Not optional/aspirational here — enforced
  in practice across this codebase's history.
- **i18n**: flat `"namespace.key": "template {placeholder}"` maps in
  `locales/en.json`/`ru.json`, looked up via `services/i18n.py`'s
  `t(key, lang, **kwargs)`. Every reply-sending handler reads
  `settings.get_language(session)` once and threads `lang` through every
  `t()`/keyboard call. Missing key in `ru.json` → falls back to
  `en.json` + WARNING; missing in both → ERROR + returns the bare key
  (never raises — a bad translation shouldn't crash a live bot). Brand
  names (AniList/Shikimori/Jikan/TMDB) and the `/language` picker's own
  native-name labels are deliberately never translated.
- **Logging**: loguru, level chosen by "would this help at
  `LOG_LEVEL=INFO` in prod, or only while debugging" — DEBUG=routine
  internal detail, INFO=meaningful game event, WARNING=recoverable
  anomaly/rejected action, ERROR=something actually broken. New handler/
  service function/job callback gets its logging in the *same commit*
  that adds the behavior, not a later sweep — this table sat unapplied
  for two release rounds once already and the gap cost real debugging
  time (see `CLAUDE.md`'s Logging section for the incident).
- **Never resolve a qlty/ruff/ty finding by loosening the check**
  (raising a threshold, disabling a rule, excluding a path) — fix the
  code. If a finding is genuinely a false positive, say so explicitly
  and get human confirmation before touching config; never default to
  loosening unilaterally.
- **"Before adding something new"** (`ARCHITECTURE.md`): a genuinely new
  file/module/enum/shared concept gets its placement decided explicitly
  and that section of `ARCHITECTURE.md` updated — don't drop it into
  whichever file happens to need it first.
- Branch convention for a batch of related fixes to one subsystem: a
  shared `fix/<area>-hardening`-style branch with sequential commits
  (precedent: `fix/search-service-hardening`, used twice), rather than
  one PR per unrelated-looking issue that's actually part of the same
  hardening effort.
