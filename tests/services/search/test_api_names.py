"""Every provider module's display string comes from `Provider`, not its
own inline literal (issue #97).

`RestApi.name` and `parsing`/`http_retry`'s `api_name`/`service_name`
stay plain `str` on purpose — they are interpolated straight into log
lines and messages, and typing them as `Provider` would mean an explicit
`.display_name` at every one of those sites, where a single miss
silently downgrades "Jikan" to "jikan" with no error. Fixing the string
at its *construction* site instead is what these tests pin: "Jikan" the
literal exists once, in `Provider.JIKAN.display_name`."""

import pytest

from nani_pix_bot.models.enums import Provider
from nani_pix_bot.services.search import anilist, jikan, shikimori, tmdb


@pytest.mark.parametrize(
    ("api_name", "provider"),
    [
        (jikan._API.name, Provider.JIKAN),
        (shikimori._API.name, Provider.SHIKIMORI),
        (tmdb._API.name, Provider.TMDB),
        # AniList talks GraphQL rather than going through rest.RestApi,
        # so its display string is a module constant instead — used at
        # all three of its inline sites (parse_entries, parse_entry and
        # request_with_retry's service_name).
        (anilist._API_NAME, Provider.ANILIST),
    ],
)
def test_provider_modules_name_themselves_from_the_enum(api_name: str, provider: Provider) -> None:
    assert api_name == provider.display_name
