"""Season visual styles (seasons spec §5): a post-processor over a season
game's pixelated stage image, under the host's frame. A run names its style
by key; the drawing code lives here, outside the frozen run modules."""

from collections.abc import Callable, Mapping
from io import BytesIO
from types import MappingProxyType

from loguru import logger
from PIL import Image
from sqlalchemy.orm import Session

from nani_pix_bot.models.game import Game
from nani_pix_bot.seasons.definition import SeasonRun
from nani_pix_bot.services.seasons import modifiers, moonlit, watercolor


def _plain(image: Image.Image) -> Image.Image:
    return image.convert("RGB")


STYLES: Mapping[str, Callable[[Image.Image], Image.Image]] = MappingProxyType(
    {"plain": _plain, "watercolor": watercolor.watercolor, "moonlit": moonlit.moonlit}
)


def style_for(session: Session, game: Game) -> str | None:
    """The style key of the game's season run; None for an untagged game."""
    run = modifiers.season_of(session, game)
    return run.style if run is not None else None


def apply_style(png: bytes, key: str) -> bytes:
    draw = STYLES.get(key)
    if draw is None:
        logger.warning("unknown season style {style!r} — posting the stage unstyled", style=key)
        return png
    out = BytesIO()
    draw(Image.open(BytesIO(png))).save(out, format="PNG")
    return out.getvalue()


def problems(run: SeasonRun) -> list[str]:
    """Everything wrong with `run`'s style key; [] when it is valid."""
    if run.style is not None and run.style not in STYLES:
        return [f"style {run.style!r}: no such style"]
    return []
