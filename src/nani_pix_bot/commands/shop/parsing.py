"""Parsing of the shop's `shop:buy:` / `shop:tile:` callback data."""

from nani_pix_bot.models.enums import ClueKind
from nani_pix_bot.services.clues.shop import TILE_GRID

_BUY_PARTS = 4  # shop:buy:<game_id>:<kind>  and  shop:tile:<game_id>:<index>
# shop:buy:<game_id>:screenshot:<owned count when the menu was drawn>
_SCREENSHOT_BUY_PARTS = _BUY_PARTS + 1


def parse_buy(data: str) -> tuple[int, ClueKind, int | None] | None:
    """(game_id, kind, owned-screenshot count) from a `shop:buy:` button;
    the count is required for screenshots and rejected for other kinds."""
    parts = data.split(":")
    is_screenshot = len(parts) > _BUY_PARTS - 1 and parts[3] == ClueKind.SCREENSHOT.value
    if len(parts) != (_SCREENSHOT_BUY_PARTS if is_screenshot else _BUY_PARTS):
        return None
    try:
        count = int(parts[4]) if is_screenshot else None
        return int(parts[2]), ClueKind(parts[3]), count
    except ValueError:
        return None


def parse_tile(data: str) -> tuple[int, int] | None:
    """(game_id, tile index) from `shop:tile:<gid>:<index>`; None unless
    the index is an integer inside the grid."""
    parts = data.split(":")
    if len(parts) != _BUY_PARTS:
        return None
    try:
        game_id, index = int(parts[2]), int(parts[3])
    except ValueError:
        return None
    return (game_id, index) if 0 <= index < TILE_GRID * TILE_GRID else None
