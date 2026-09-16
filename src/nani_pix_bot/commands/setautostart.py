"""/setautostart on|off — DM-only, admin-gated. Lets an admin allow or
disallow the bot starting a game itself (idle auto-start, or "overthrow"
right after a game concludes) — independent of, and checked in addition
to, /setgamesenabled."""

from nani_pix_bot.commands.helpers.admin_toggle import (
    AdminToggleConfig,
    make_admin_toggle_command,
)
from nani_pix_bot.services import settings

setautostart_command = make_admin_toggle_command(
    AdminToggleConfig(
        command_name="setautostart",
        usage_key="autostart.usage",
        enabled_key="autostart.enabled",
        disabled_key="autostart.disabled",
        setter=settings.set_autostart_enabled,
    )
)
