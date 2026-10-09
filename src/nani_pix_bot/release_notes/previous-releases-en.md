## v1.14.1 · 2026-10-09

### 🛠 Fixed
- `/version` shows the release notes again — it came up empty when the bot's language was set to Russian.

## v1.14.0 · 2026-10-09

### ✨ New
- Champion races now reward a clean solve: win without a single wrong guess of your own and you get +1 🌟 on top (not in HARD MODE).
- Ties in the weekly, monthly and yearly races are settled in the open — more 👑 wins, then fewer ❌ wrong guesses in the games you won, then faster ⏱ solves — and anyone still tied shares the place, with both new columns right there in `/standings`.
- Everyone tied for first when a race closes becomes Champion, up to three of you.
- Your own `/achievements` browser has a ⚖️ Compare button that lists players by 🏆 points, so you can pick anyone to measure yourself against.
- `/history` has filter tabs: 📜 All · 👤 I played · 🎲 I hosted · 👑 I won.
- A game's record in `/history` is split into 📋 Record and 💬 Guesses tabs and now shows the clues bought (with price and who shared them), who chipped 💠 into the bounty, when and why each stage cleared up, and how many players guessed.
- `/version` shows these notes, with ◀ ▶ to look back at earlier releases.

### 🛠 Fixed
- The 📏 Title shape that comes again after you buy a letter has its own Share button, and you can share it again once it shows a new letter.
- Oversized messages, like a long `/history` page, are trimmed instead of silently never arriving.

## v1.13.0 · 2026-10-08

### ✨ New
- Every ending now plays an animated reveal: the pixels give way to the clear screenshot through one of five effects (iris, tile flip, ripple, glitch or shatter), and a win adds a gold badge with the winner's avatar, a crown and confetti.
- `/history` in DM lists past games with how each one ended and every guess made.
- `/status` in the game topic re-posts the current image with a live caption, for when the pinned one has scrolled out of sight.
- `/leaderboard` is now a paged table with 👑 wins, 💠 pixels and 🏆 achievement points side by side.
- `/standings` explains how 🌟 points work and shows this week's latest 🌟 gains in DM.
- The win message now says whose 🌟 points went up and what they count towards.

### 🛠 Fixed
- Milestone Keeper now says it counts the group's solved games (not the 🎲 game number) and shows how far along the group is.
- Setting up a round no longer loses a step you were already looking at when Telegram or a search is slow, and the preview no longer makes the whole bot stall while it renders.

## v1.12.0 · 2026-10-07

### ✨ New
- Achievements are here: a whole catalogue of them (some secret) that pay 💠 pixels and 🏆 points, with every unlock announced in the game topic on a card with your avatar.
- `/achievements` in DM lets you browse yours or anyone's by tabs and pages, and compare two players side by side.
- `/achievements` in the group shows a summary, and `/achievements top` ranks everyone by 🏆 points.
- The top tier of an achievement unlocks a title: pick yours with `/title` and it shows next to your name on the `/leaderboard`.
- Weekly, monthly and yearly champion races: every win scores 🌟 points, and `/standings` shows the live tables.
- When a race closes, its podium is posted on a card, and the winner becomes Champion of the week, month or year, title included.
- Every win message shows the 🌟 points it just earned.
