# Mechanics

The single source of truth for this game's rules. See `ARCHITECTURE.md` for
how these rules map onto code/data, and `CLAUDE.md` for repo conventions.
Each section below is tagged **Status: Implemented** or **Status:
Planned** so this doc describes the target design across all of v1, not
just what's currently built.

## Status overview

| Feature | Status |
|---|---|
| Game setup (DM photo + AniList/Shikimori/manual entry) | Implemented |
| Group-membership gate on DM setup | Implemented |
| Pixelation stages (x10 → x8 → x5 → x2 → reveal) | Implemented |
| Guess matching (`/guess`, local fuzzy match) | Implemented |
| Author override (`/correct`) | Implemented |
| Turn handoff (`/skip`) | Implemented |
| 2-day timeout | Implemented |
| Setup-abandon timeout (1h) | Implemented |
| Win-turn reminder (15min) + expiry (12h) | Implemented |
| Leaderboard (`/leaderboard`) | Implemented |
| Deployed to `moscow` | Implemented |

## Starting a game

**Status: Implemented.**

A game can only start if no other game is currently `SETUP` or `ACTIVE`.
The player allowed to start is whoever `turn_state.next_starter_id` names —
or **anyone**, if it's `null`. They must also currently be a member of the
configured group — the DM entry point is reachable by anyone who finds the
bot, unlike the topic-scoped group commands, which Telegram itself already
restricts to members.

1. The eligible player DMs the bot a screenshot (a photo). The bot posts
   a notice to the group topic — "so-and-so is preparing a new game" —
   so nobody else tries to DM a photo at the same moment, and starts a
   **1-hour setup-abandon timer** (`Game.setup_deadline`). If the player
   never reaches the preview's "Confirm and start" button within that
   hour, the bot deletes the orphaned `SETUP` row, opens the turn to
   anyone, and posts that to the group — the same class of "stuck DM
   flow" bug issue #11 fixed reactively can no longer linger
   indefinitely. The timer is canceled the moment they confirm.
2. The bot asks them to pick an identification method: **AniList**,
   **Shikimori** (the Russian-community anime database, with better
   Russian titles/synonyms), or **manual entry**. If the bot's language
   is currently Russian, Shikimori is listed first with a one-line note
   explaining why. The choice is stored on the game's still-`SETUP` row
   (`Game.source`), not in memory, so it survives a restart before the
   player finishes typing.
3. **AniList/Shikimori**: the bot asks for a search query (the anime's
   name, in whatever form the player remembers it) and searches whichever
   service was picked, showing up to 5 results as an inline keyboard
   (title + year for AniList, the Russian title for Shikimori); a "none
   of these" option lets them retry the search with different text. The
   player taps the correct result, and the bot re-fetches the full record
   from that service (title romaji/English/native for AniList, plus a
   Russian title for Shikimori) and its synonyms list.
   **Manual entry**: for anime neither service knows about. The bot asks
   for the title, then for at least one alternate title/synonym
   (comma- or newline-separated, re-prompted if left blank) — both typed
   by the player, no external lookup.
4. Either way, once a title/synonyms are staged, the bot sends the player
   a **private preview** — the x10-pixelated screenshot plus the staged
   title and full synonyms list — instead of posting straight to the
   group. Four buttons let them fix anything before it goes live:
   - **Change image** — send a new screenshot; keeps the title/synonyms.
   - **Re-search title** — back to the method-selection keyboard; keeps
     the screenshot, replaces the title/synonyms/source once a new one
     is staged.
   - **Add a synonym** — type one more (or several); appended to the
     list, repeatable.
   - **Confirm and start game** — pixelates the (possibly updated)
     screenshot at **x10** and posts it into the group's game topic with
     a caption naming the starter and reminding everyone how to guess
     (`/guess <title>` in that topic). The game is now `ACTIVE`.
   Exactly where the starter is in this multi-step flow
   (`Game.setup_step`) is stored on the row, not in memory, so a restart
   mid-edit — say, between tapping "Add a synonym" and typing it —
   resolves correctly from the DB rather than losing track.

If the chosen service is unreachable (search or re-fetch fails), the bot
tells the player and hands back the method-selection choice so they can
try again or switch services — the game stays `SETUP`, never stuck.

If someone who isn't the designated starter (and it isn't open) DMs a
photo, the bot replies that it isn't their turn and doesn't create a game.
Same for someone who isn't currently a group member.

## Guess matching

**Status: Implemented.**

Guessing is an explicit command — **`/guess <text>`** — sent in the game
topic, never free text. This is deliberate: it means the bot never needs
Telegram's group-privacy mode disabled, and every guess attempt is an
unambiguous, loggable event rather than "was that message meant as a
guess?"

Matching is **local and fully deterministic** — no network call happens
per guess. Whichever service was used at setup (see above) is only ever
consulted once, and its result (title variants — including the Russian
title, if Shikimori was used — plus synonyms) is cached on the `Game`
row for the rest of that round:

1. Normalize both the guess and every cached title/synonym: lowercase,
   strip punctuation and extra whitespace.
2. Fuzzy-match the normalized guess against that normalized list with
   `rapidfuzz`, above a fixed similarity threshold defined as a named
   constant in `services/matching.py`.
3. Any match above the threshold counts as correct — it doesn't matter
   which title/synonym it matched, or by how much it cleared the
   threshold.

A **wrong** guess isn't silent: the bot replies in-topic with how many
more wrong guesses remain before the next pixelation stage, and which
stage the game is currently on (e.g. "3 guesses left before the next
clue (1/4)"). The player who started the round can't `/guess` on their
own game at all — they already know the answer.

Because everything the matcher needs is cached at setup time, the same
guess always produces the same verdict for the life of a game — matching
never depends on AniList/Shikimori being reachable, rate limits, or
anything else external, at guess time.

## Pixelation stages

**Status: Implemented.**

The screenshot is downscaled (blocky pixelation) to a **fixed target
width** and immediately upscaled back to its original size, via Pillow,
at four decreasing widths, from blockiest to clearest. A fixed target
width — not a divisor of the source resolution — keeps stage difficulty
independent of the screenshot's resolution: a 12px-wide image reads as
pure color blobs whether the original screenshot was 1280px or 3840px
wide, whereas e.g. ÷10 of a 2560px-wide screenshot is still 256px wide
and barely pixelated at all. Values chosen by eye against real
screenshots:

| Stage | Target width |
|---|---|
| `X10` | 12px (shown first — hardest) |
| `X8` | 24px |
| `X5` | 48px |
| `X2` | 64px (clearest pixelated stage) |

No image bytes are stored on disk or in the database. The starter's
original screenshot is kept only as a Telegram `file_id` on the `Game`
row; every stage image is regenerated on demand — download the original
via that `file_id`, run it through the Pillow pipeline, send it, discard
the bytes — so a bot restart mid-game loses nothing (the `file_id` and
`current_stage` are all that's needed to pick back up).

**Every 5 wrong `/guess` attempts, the game advances to the next stage**
and `wrong_guess_count` resets to 0. This constant (5) lives in
`services/game.py`. If the 5th wrong guess lands while already at `X2`,
the game ends unsolved (see below) instead of advancing further.

## Winning

**Status: Implemented.**

A game ends in a win one of two ways:

- **Automatic**: a `/guess` matches per the rules above.
- **Author override (`/correct @username`)**: the game's starter can force
  a win for a specific player at any time while the game is `ACTIVE`, for
  the case where the fuzzy matcher fails to recognize a guess that was
  actually correct (an alternate title/spelling AniList doesn't list as a
  synonym, for example). Only the starter may run this command; it's a
  fixed `@username` argument, not a reply-based selection.

On a win, the bot:
1. Reveals the original (un-pixelated) screenshot together with the
   anime's title, naming the winner by name in the caption.
2. Sets `status → WON`, records `winner_id`, and increments that player's
   `players.wins`.
3. Cancels the game's pending 2-day timeout job.
4. Sets `turn_state.next_starter_id` to the winner — it's their turn to
   start the next game, called out explicitly in the reveal caption —
   and schedules that winner's 15-minute reminder / 12-hour expiry (see
   "Turn handoff" below).
5. **Cleanup**: once that reveal message is confirmed sent, clears
   `Game.original_file_id` — nothing after this point ever needs to
   re-fetch or re-pixelate the screenshot, so the stored Telegram file
   reference is dropped rather than kept around indefinitely.

## Ending unsolved

**Status: Implemented.**

A game ends unsolved one of two ways, handled identically:

- **Stage exhaustion**: the 5th wrong guess lands while already at the
  `X2` stage.
- **Timeout**: see below.

Either way, the bot reveals the original screenshot with the anime's
title, sets `status → UNSOLVED`, and performs the same `original_file_id`
cleanup described in "Winning" above. `turn_state.next_starter_id` is left
untouched — an unsolved game doesn't hand anyone a forced turn. If it was
already `null` (open to anyone), it stays that way; if someone was
designated (unusual for this path, since normally only a win sets it),
they remain designated.

## Timeout

**Status: Implemented.**

Every `ACTIVE` game gets a **2-day timeout, absolute from game start** —
not reset by guessing activity. It's scheduled as a `JobQueue` job at
`created_at + 2 days` (`scheduled_end_at`) when the game goes `ACTIVE`. If
no one has won by then, the job fires the same "ending unsolved" flow
above, regardless of how many wrong guesses happened in between.

Because `JobQueue` jobs don't survive a process restart, `app.py` re-arms
a timeout job on startup for any `Game` still `ACTIVE`, using its stored
`scheduled_end_at` — a redeploy never silently loses or resets the clock.

## Turn handoff (`/skip`)

**Status: Implemented.**

Usable only by whoever `turn_state.next_starter_id` currently names, and
only while no game is `SETUP`/`ACTIVE` (it governs who may *start* the
next game, not anything mid-game):

- `/skip` (no argument) sets `next_starter_id` to `null` — the turn opens
  up, and anyone can DM the bot a screenshot to start the next game.
  Cancels the reminder/expiry timers below.
- `/skip @username` hands the designation directly to that person instead
  — (re)schedules the timers below for the new designee.

Whenever `next_starter_id` becomes a real user (a win, or `/skip @user`),
two absolute-deadline `TurnState` timers are (re)scheduled:

- **Reminder (15 minutes)**: DMs the designated player that it's their
  turn. If the DM fails (they've never started the bot), falls back to
  an `@mention` in the group topic instead — same "can't reach them
  privately" fallback pattern as `/help`'s.
- **Expiry (12 hours)**: if they still haven't started by then, opens the
  turn to anyone (same as a bare `/skip`) and posts that to the group.

Both are canceled — without touching `next_starter_id` itself — the
moment the designated player actually DMs a photo to start their game;
they're clearly not going to miss a turn they've already begun.

## Leaderboard

**Status: Implemented.**

`/leaderboard`, usable at any time in the game topic regardless of whether
a game is running, lists players ordered by `players.wins` descending.
