"""Anime-identification search — AniList, Shikimori, Jikan, and TMDB,
called once per game at setup time only (see MECHANICS.md's "Starting
a game" and "Guess matching" sections: none of them is ever consulted
per guess, only to populate a Game's cached title/synonyms). All four
share the same rate-limit-retry shape against their respective
external APIs — see http_retry.py.

This package exposes `anilist`/`shikimori`/`jikan`/`tmdb` as
submodules — callers do `from nani_pix_bot.services.search import
anilist, shikimori, jikan, tmdb` — since each has its own result
dataclass, search()/get_by_id() pair, and service-specific quirks
(AniList's GraphQL API vs. the other three's REST APIs, Shikimori's
list/detail endpoint split vs. Jikan/TMDB returning the full field set
on both, TMDB's API-key/proxy requirement vs. the other three being
keyless and reachable direct from moscow) that don't collapse into one
shared surface."""
