"""/setgamesenabled on|off — DM-only, admin-gated. Lets an admin lock
out *starting* new games entirely (e.g. while mid stage-config tuning
via /setstageconfig or /setstage), independent of /stop (which ends a
game already in progress)."""

from telegram.ext import ContextTypes

from nani_pix_bot.commands.helpers.admin_toggle import (
    AdminToggleConfig,
    make_admin_toggle_command,
)
from nani_pix_bot.jobs import timers as timeout_module
from nani_pix_bot.services import settings


def _arm_boss(context: ContextTypes.DEFAULT_TYPE) -> None:
    """A Gashadokuro boss held while games were off may start now."""
    timeout_module.schedule_boss_start(context.job_queue)


setgamesenabled_command = make_admin_toggle_command(
    AdminToggleConfig(
        command_name="setgamesenabled",
        usage_key="gamesenabled.usage",
        enabled_key="gamesenabled.enabled",
        disabled_key="gamesenabled.disabled",
        setter=settings.set_games_enabled,
        on_enabled=_arm_boss,
    )
)
