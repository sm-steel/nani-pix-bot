## v1.19.0 · 2026-10-17

### ✨ New
- 🙅 The HARD MODE vote has a **Nobody** button for when no guess was right, and it no longer has to run its full 15 minutes: as soon as one option has the most votes, with no tie, from at least half of the day's players (never fewer than 3), it closes on the spot.
### 🛠 Fixed
- 📷 Sent a different screenshot while setting up a round, before picking the anime (say, after the season's theme turned the first one down)? It now replaces the first one, instead of being ignored while the round went live with the old picture.

## v1.18.0 · 2026-10-17

### ✨ New
- 🖼 Starting a round? The preview now has **Clue screenshots**: choose whether the shop's extra screenshot is random (as before), only your own picks, or yours first and then random. Add up to 10 from the Shikimori or TMDB gallery or upload your own; the ones handed out show up in the clue album when the round ends.
- 🖼 When a round ends, every extra screenshot bought during it is shown in full quality in an album under the reveal. In HARD MODE the video now shows one screenshot, and the other one opens the album.

### 🛠 Fixed
- 🖼 An extra screenshot from `/shop` is always a new one now: never the round's own picture, never one you or anyone else already got that round (even if it was refunded), and picked at random instead of the same one for everybody.

## v1.17.0 · 2026-10-14

### ✨ New
- 🏳 Stuck on a screenshot nobody knows? `/pass` votes to give up: once enough of the group agrees (half of those who played in the last day, at least 3), the round ends and the answer is shown.
- 🌸 In a season, an anime that's off-theme is turned down the moment you pick it — from search, your MAL list or a typed title — instead of after you've chosen a screenshot.
- ↩ Picked the wrong anime while setting up a game? **Pick a different anime** now sits under the screenshot menu, the gallery and the setup questions, so there's no need to `/stop` and start over.
- 🎫 `/seasonpass` is easier to read: the tiers are a table with the free and ⭐ Premium rewards in their own columns, a ⬜ the same size as ✅ for tiers still ahead, a 🔒 on the Premium column until you buy it, and the objectives under a heading of their own.
- 🪪 `/profile`'s ◀ ▶ and 👥 Players now walk only the people who actually play, most games first — no more lurkers, and never Nani Pix herself.
- 🎨 Cosmetic previews in `/cosmetics` and `/seasonshop` now show you — your name with the flair, your avatar and name on the win badge, your own profile card behind a banner — and stage frames and reveal transitions play out on a real pixelated anime screenshot, just as a round posts them.

## v1.16.0 · 2026-10-11

### ✨ New
- 🌸 **Sakura Season** is the first real season: slice-of-life, romance and school anime in a watercolor style, clues 25% cheaper, a 12-tier pass with free and ⭐ Premium rewards (Premium is 300 💠), 20 cosmetics, a Season Shop, seven objectives and achievements of its own, including a hidden one.
- 🏅 Season stamps now carry the season's art: everyone who scored gets the Sakura stamp, finishing the free pass puts a gold ring on it, and a Premium tier turns it into the golden Sakura+.
- 🖼 Season posts and profile cards have a new look: the season's key visual with no darkening, a ribbon, a gradient title beside the stamp seal and a 🗓 date pill, and a profile card with outlined text, counter chips and a glass grid that adapts to light and dark pictures.
- 🎉 When a game ends, every player who guessed in it is checked for achievements, not only the winner and the host.
- 👮 `/setwinner` on a season game that ended unsolved now tops the host up to the solved reward instead of paying both, quick date edits of a scheduled season share one teaser post, and the season's "only this kind of anime" message and the advice for an anime whose genre can't be found are clearer.
- 📖 There's now an illustrated guide, in English and Russian, covering how to play, every command and how to run your own Nani Pix: https://sm-steel.github.io/nani-pix-bot/
- 👮 Admins can schedule and manage seasons with `/season`.
- 🎁 Admins can hand out season cosmetics with `/grantitem`.
- 🪪 `/profile [@username]` shows a player's profile card: avatar, title, a stamp for every season played and how many cosmetics they own. ◀ ▶ on the card flip through everyone's cards, and 👥 Players lists them all. Up to four of your rarest achievements show too, and ✏️ on your own card (in a private chat) lets you pick which achievements and season stamps appear.
- 🎨 `/cosmetics` (in a private chat) lists the season cosmetics you own by slot, previews each one — as an image, a short clip or a line of text — and lets you equip it: a win effect, a reveal transition and a stage frame for your rounds, a name flair emoji after your name in the group, a reaction on your winning guess, a profile banner and season titles.
- 🪙 Season games earn Season Coins for wins, hosting and first guesses, and a Season Pass with free and ⭐ Premium rewards (Premium costs 💠) pays out cosmetics, coins, 💠 and clue tokens as your Season XP grows: see it with `/seasonpass`, and leftover 🪙 turn into 💠 when the season ends.
- 🎟 A clue token makes your next clue in the shop free, and you get it back if the clue can't be delivered.
- 🛍 A season may open a Season Shop: `/seasonshop` (in a private chat) lets you look at an item first, then buy it with 🪙 — cosmetics once each, clue tokens 🎟 up to a limit — and you put cosmetics on in `/cosmetics`.
- 🎯 A season may set objectives: one-time goals that pay Season XP and 🪙, with a private message when you finish one and your progress, and the date you finished, in `/seasonpass`.
- 🌸 A season may make clues cheaper in its games, stacking with the HARD MODE sale, and may paint its games' stage pictures in its own style.
- 🏆 Seasons have achievements: eleven you can earn once in every season (Season Debut, Opening Act, finishing the pass, the Season Shop, a place on the podium and the "Champion of" title, and more) shown as ×N in `/achievements`, six lifetime ladders across seasons (Veteran, Completionist, Front-runner, Hoarder, Season Patron and Genre Explorer, which counts the themes you won in, Nani Pix's own HARD MODE picks included), and a season may bring achievements of its own.
