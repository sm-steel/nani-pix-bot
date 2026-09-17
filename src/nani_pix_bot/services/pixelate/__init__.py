"""Public surface for the pixelation package — every existing
`from nani_pix_bot.services import pixelate as pixelate_service` call
site keeps working unchanged via attribute access on this re-exported
API, and `from nani_pix_bot.services.pixelate import pixelate` still
resolves too.

algorithms.py holds the `PixelAlgorithm` → implementation registry and
the `pixelate()` entry point; render.py holds the Pillow primitives
those implementations are composed from.

The enum itself — and `DEFAULT_ALGORITHM`/`DISCOURAGED_ALGORITHMS`
alongside it — lives in models/enums.py with the rest of the persisted
enums, and is imported from there rather than re-exported here: it is
a column on `games` (the starter picks one per game, and every later
stage has to be rendered the way the first one was), so models/game.py
needs the default and models must not depend on services.

See MECHANICS.md for what the choice means in play, and CLAUDE.md's
test-driven-development note for why this package's tests came first.
"""

from nani_pix_bot.services.pixelate.algorithms import ALGORITHMS, pixelate

__all__ = [
    "ALGORITHMS",
    "pixelate",
]
