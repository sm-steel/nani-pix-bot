"""The one answer to "is MAL account linking configured on this bot?",
shared by everything that has to ask — the method picker's 6th button
(via commands/dm_start/_shared.py, preview.py and mal_browse.py) and
/linkmal (commands/mal_link.py).

Lives in commands/helpers/ for the same reason scoping.py and
membership.py do: it's a small predicate over Telegram-layer state
(`bot_data`, filled in by app.py's build_application) that several
command modules share, and nothing in services/ or models/ has any
business reading. It imports nothing from commands/, so no module that
needs it can create a cycle by importing it.

Three earlier answers to the same question disagreed: the button gate
checked `mal_client_id` alone, /linkmal checked `mal_client_id` and
`mal_redirect_uri`, and ARCHITECTURE.md/README.md both claimed all four
variables were required. That gap was reachable and expensive — with
MAL_CLIENT_ID set but MAL_TOKEN_ENCRYPTION_KEY unset, a player could
complete a real OAuth consent at MyAnimeList, paste the code back, and
have `token_crypto.encrypt(None, ...)` raise AttributeError (not
InvalidToken, so nothing caught it) after their single-use
authorization code had already been spent. All four or nothing."""

from collections.abc import Mapping
from typing import Any

# Every bot_data key app.py fills in from the four MAL_* environment
# variables. Named here rather than spelled out at each call site so
# adding a fifth would be a one-line change in one place.
MAL_BOT_DATA_KEYS = (
    "mal_client_id",
    "mal_client_secret",
    "mal_redirect_uri",
    "mal_token_encryption_key",
)


def mal_configured(bot_data: Mapping[str, Any]) -> bool:
    """True only when all four MAL settings are present. A partial
    configuration counts as unconfigured: the method button stays hidden
    and /linkmal says so, rather than walking a player into a flow that
    cannot finish. app.py warns at startup when it sees a partial one —
    that warning is what tells the operator which variable is missing."""
    return all(bot_data.get(key) for key in MAL_BOT_DATA_KEYS)
