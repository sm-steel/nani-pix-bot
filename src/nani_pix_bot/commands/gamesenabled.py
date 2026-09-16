"""/setgamesenabled on|off — DM-only, admin-gated. Lets an admin lock
out *starting* new games entirely (e.g. while mid stage-config tuning
via /setstageconfig or /setstage), independent of /stop (which ends a
game already in progress)."""

from nani_pix_bot.commands.helpers.admin_toggle import (
    AdminToggleConfig,
    make_admin_toggle_command,
)
from nani_pix_bot.services import settings

setgamesenabled_command = make_admin_toggle_command(
    AdminToggleConfig(
        command_name="setgamesenabled",
        usage_key="gamesenabled.usage",
        enabled_key="gamesenabled.enabled",
        disabled_key="gamesenabled.disabled",
        setter=settings.set_games_enabled,
    )
)
