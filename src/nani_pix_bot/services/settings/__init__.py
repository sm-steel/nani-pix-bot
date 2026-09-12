"""Bot-wide configuration: `bot_settings` (singleton row — language,
games_enabled) and `stage_config` (one row per PixelStage — pixelation
width/wrong-guess-limit). Same domain, two different persistence
shapes — see each submodule's own docstring.

This package re-exports `bot_settings`'s functions directly (so
existing `from nani_pix_bot.services import settings` +
`settings.get_language(...)` call sites are unaffected) and exposes
`stage_config` as a submodule (callers do
`from nani_pix_bot.services.settings import stage_config`)."""

from nani_pix_bot.services.settings import stage_config
from nani_pix_bot.services.settings.bot_settings import (
    DEFAULT_GAMES_ENABLED,
    DEFAULT_LANGUAGE,
    SETTINGS_ID,
    get_games_enabled,
    get_language,
    set_games_enabled,
    set_language,
)

__all__ = [
    "DEFAULT_GAMES_ENABLED",
    "DEFAULT_LANGUAGE",
    "SETTINGS_ID",
    "get_games_enabled",
    "get_language",
    "set_games_enabled",
    "set_language",
    "stage_config",
]
