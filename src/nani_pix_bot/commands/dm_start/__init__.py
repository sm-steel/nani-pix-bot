"""Private-chat photo intake + anime-identification (AniList/Shikimori)
search-and-pick flow — see MECHANICS.md's "Starting a game" section.

Deliberately keeps no state in PTB's in-memory `user_data`: which game a
DM is setting up, and which method (AniList/Shikimori) it's using, are
both derived from the DB (`get_setup_game_for_starter`, `Game.source`),
and which search result a tapped button means is re-fetched by id
(`anilist.get_by_id`/`shikimori.get_by_id`) rather than cached — all of
this survives a bot restart mid-setup, which in-memory `user_data`
doesn't (see issue #11).

Split across submodules by flow stage:
  intake.py    photo_handler — the entry point
  search.py    method selection + AniList/Shikimori search-and-pick
  manual.py    manual title/synonym entry
  preview.py   the confirmation preview (show/confirm/change-image/
               research/add-synonym) and the final post to the group
  keyboards.py inline-keyboard builders + callback-data constants
  _shared.py   small helpers used by more than one of the above

This __init__ re-exports only the PTB handler entrypoints app.py
registers — not a blanket re-export of every submodule's internals."""

from nani_pix_bot.commands.dm_start.intake import photo_handler
from nani_pix_bot.commands.dm_start.preview import preview_callback_handler
from nani_pix_bot.commands.dm_start.search import (
    method_pick_callback_handler,
    pick_callback_handler,
    search_text_handler,
)

__all__ = [
    "method_pick_callback_handler",
    "photo_handler",
    "pick_callback_handler",
    "preview_callback_handler",
    "search_text_handler",
]
