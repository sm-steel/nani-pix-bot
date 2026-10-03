"""The 💠 pixel economy — see the pixel-economy design spec and
MECHANICS.md's "Pixels" section. Import submodules directly
(`from nani_pix_bot.services.economy import wallet`); this package
deliberately re-exports nothing, because services/players.py imports
config/wallet while earning.py imports services.game, which imports
players — an eager re-export here would be an import cycle."""
