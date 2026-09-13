"""Anime-identification search — AniList, Shikimori, and Jikan, called
once per game at setup time only (see MECHANICS.md's "Starting a game"
and "Guess matching" sections: none of them is ever consulted per
guess, only to populate a Game's cached title/synonyms). All three
share the same rate-limit-retry shape against their respective
external APIs — see http_retry.py.

This package exposes `anilist`/`shikimori`/`jikan` as submodules —
callers do `from nani_pix_bot.services.search import anilist,
shikimori, jikan` — since each has its own result dataclass,
search()/get_by_id() pair, and service-specific quirks (AniList's
GraphQL API vs. Shikimori's/Jikan's REST APIs, Shikimori's list/detail
endpoint split vs. Jikan returning the full field set on both) that
don't collapse into one shared surface."""
