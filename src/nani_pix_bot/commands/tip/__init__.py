"""/tip — send currency to another player, in a DM or the game topic, by
@username or from a player picker. See services/economy/tips.py for the rules."""

from nani_pix_bot.commands.tip.callback import tip_callback
from nani_pix_bot.commands.tip.command import tip_command
from nani_pix_bot.commands.tip.views import PREFIX

__all__ = ["PREFIX", "tip_callback", "tip_command"]
