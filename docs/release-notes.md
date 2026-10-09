# Release notes

`/version` shows hand-written, player-facing notes for the running release,
with ◀ ▶ to page back through up to four earlier ones. They are not the
GitHub release body: that one is semantic-release's raw commit list (every
`fix:` subject, PR numbers, hashes, fixes of bugs that never shipped), which
is for developers. These notes are for the group.

## Files

They live in the package, so the Docker image carries them:

```
src/nani_pix_bot/release_notes/
  release-notes-en.md      # the release being developed (no version heading)
  release-notes-ru.md
  previous-releases-en.md  # up to four shipped releases, newest first
  previous-releases-ru.md
```

- **`release-notes-<lang>.md`** holds the notes of the release that `develop`
  is building. Once it ships, they are the running release's notes, and
  `/version` puts the running version (`installed_version()`) above them, so
  the file has **no version heading**. An empty file is allowed: `/version`
  then shows the header and "Nothing noted for this build yet."
- **`previous-releases-<lang>.md`** holds the four releases before that, newest
  first, each under a heading `## vX.Y.Z · YYYY-MM-DD`:

  ```markdown
  ## v1.13.0 · 2026-10-08

  ### ✨ New
  - ...
  ```

`services/release_notes.py` reads them (`load(lang)`: page 0 is the current
file, the rest are split on the `## ` headings); `commands/version.py` sends
them as a rich message and pages with `ver:<page>`.

## What a release's notes look like

- Two optional sections, in this order: `### ✨ New` and `### 🛠 Fixed`. Each
  is a bullet list (`- `) of **at most 8 bullets**. Nothing else: no other
  headings, no prose between sections.
- **One sentence per bullet**, written for players, in the bot's own playful
  voice (read `locales/en.json` / `ru.json` for it). Use the bot's symbols
  (💠 pixels, 🌟 champion points, 👑 wins, 🏆 achievement points, ❌ wrong
  guesses, ⏱ solve time) and write commands in code spans: `` `/history` ``.
- **Admin-only** lines start with 👮.
- Order bullets by impact: what most players will notice first.
- **Never** include PR or issue numbers, commit hashes, file, module or
  function names, internals, or dev churn.

### What qualifies

Anything a player or an admin can see or feel: a new or changed command,
reply, button, rule, reward, price, timing or visual, and the fix of a bug
that was **live in a release**.

Left out: refactors, logging, CI and tooling, docs, tests, dependency bumps,
performance work unless players would notice it, and fixes of bugs that were
introduced in the same cycle and never shipped. Many commits of one feature
become one bullet.

### EN and RU stay in step

Both languages have the same sections, the same number of bullets in each
section, and the same previous-release headings. `tests/services/
test_release_notes.py` checks this, the section and bullet rules, and
forbidden tokens (`#123`, commit hashes, `.py`, `foo()` outside a code span)
on the real files, and keeps every page under 3000 characters.

## When the notes are written

- **Every PR into `develop` that qualifies adds its bullets, in both
  languages, in that same PR.** Not later, not in a sweep before the release.
- **The release PR (`develop` → `master`) only reviews the file**: prune,
  merge overlapping bullets, order by impact.
- **Nothing is committed to these files during or after a release.** A
  release is cut from `master`, and `sync-develop.yml` brings the merge back
  to `develop`; the notes ride along unchanged.

## Rotation

The first qualifying PR after a release moves the shipped notes out of the
current file. The tell that they have shipped: the latest tag is newer than
the last commit that touched `release-notes-*.md`.

```sh
git describe --tags --abbrev=0                                 # e.g. v1.14.0
git log -1 --format=%cs v1.14.0                                # its date, e.g. 2026-10-12
git log -1 --format=%cs -- src/nani_pix_bot/release_notes/release-notes-en.md
```

If the tag's commit is newer than the last change to the notes, then, for
**each language**:

1. Open `previous-releases-<lang>.md` and add, at the top, a heading
   `## v1.14.0 · 2026-10-12` (the tag name and the tag's date).
2. Under it, paste the whole content of `release-notes-<lang>.md`.
3. If the file now holds more than four releases, delete the oldest one
   (its heading and everything up to the next heading or the end).
4. Empty `release-notes-<lang>.md`, then add this PR's own bullets to it.

Run `uv run pytest tests/services/test_release_notes.py` before committing:
it catches a missing language, a fifth release or a bad heading.

## Examples

Good:

```markdown
### ✨ New
- `/history` filters: 📜 All · 👤 I played · 🎲 I hosted · 👑 I won.
- 👮 `/setwinner` lets an admin name the winner of a round nobody cracked.
```

```markdown
### ✨ Новое
- Фильтры в `/history`: 📜 Все · 👤 Я играл(а) · 🎲 Я вёл(а) · 👑 Я выиграл(а).
- 👮 `/setwinner` позволяет админу назвать победителя раунда, который никто не разгадал.
```

Not a release note: "Commit setup changes before any Telegram call (#292)",
"Pin ffmpeg 7.1.1 via mise", "Fix the reveal timeout" when the reveal itself
is new in this release.
