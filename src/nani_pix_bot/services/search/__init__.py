"""Anime-identification search — AniList and Shikimori, called once per
game at setup time only (see MECHANICS.md's "Starting a game" and
"Guess matching" sections: neither is ever consulted per guess, only to
populate a Game's cached title/synonyms). Both share the same
rate-limit-retry shape against their respective external APIs — see
http_retry.py.

This package exposes `anilist`/`shikimori` as submodules — callers do
`from nani_pix_bot.services.search import anilist, shikimori` — since
each has its own result dataclass, search()/get_by_id() pair, and
service-specific quirks (AniList's GraphQL API vs. Shikimori's REST
API with a list/detail endpoint split) that don't collapse into one
shared surface."""
