"""Anime-identification search — AniList, Shikimori, Tenrai, and TMDB,
called once per game at setup time only (see MECHANICS.md's "Starting
a game" and "Guess matching" sections: none of them is ever consulted
per guess, only to populate a Game's cached title/synonyms). All four
share the same rate-limit-retry shape against their respective
external APIs — see http_retry.py.

This package exposes `anilist`/`shikimori`/`tenrai`/`tmdb` as
submodules — callers do `from nani_pix_bot.services.search import
anilist, shikimori, tenrai, tmdb` — since each has its own result
dataclass, search()/get_by_id() pair, and service-specific quirks
(AniList's GraphQL API vs. the other three's REST APIs, Shikimori's
list/detail endpoint split vs. Tenrai/TMDB returning the full field set
on both, TMDB's API-key/proxy requirement and Tenrai's proxy-but-no-key
one vs. AniList/Shikimori being keyless and reachable directly with no
proxy at all — see app.py's build_application for the three separate
httpx clients this drives) that don't collapse into one shared
surface."""
