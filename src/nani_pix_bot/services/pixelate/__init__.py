"""Public surface for the pixelation package — every existing
`from nani_pix_bot.services import pixelate as pixelate_service` call
site keeps working unchanged via attribute access on this re-exported
API, and `from nani_pix_bot.services.pixelate import pixelate` still
resolves too.

algorithms.py holds the `PixelAlgorithm` → implementation registry and
the `pixelate()` entry point; render.py holds the Pillow primitives
those implementations are composed from. The enum itself lives in
models/enums.py, with the rest of the persisted enums — it is a column
on `games`, since the starter picks one per game and every later stage
has to be rendered the same way the first one was.

See MECHANICS.md for what the choice means in play, and CLAUDE.md's
test-driven-development note for why this package's tests came first.
"""

from nani_pix_bot.services.pixelate.algorithms import (
    ALGORITHMS,
    DEFAULT_ALGORITHM,
    DISCOURAGED,
    pixelate,
)

__all__ = [
    "ALGORITHMS",
    "DEFAULT_ALGORITHM",
    "DISCOURAGED",
    "pixelate",
]
