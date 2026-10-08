"""The reveal effects: each renders part 1 of the reveal video (issue #295)."""

from collections.abc import Callable, Mapping
from types import MappingProxyType

from PIL import Image

from nani_pix_bot.models.enums import RevealEffect
from nani_pix_bot.services.reveal.effects import glitch, iris, ripple, tile_flip

EFFECTS: Mapping[RevealEffect, Callable[[Image.Image, list[Image.Image]], bytes]] = (
    MappingProxyType(
        {
            RevealEffect.GLITCH: glitch.render_part1,
            RevealEffect.IRIS: iris.render_part1,
            RevealEffect.RIPPLE: ripple.render_part1,
            RevealEffect.TILE_FLIP: tile_flip.render_part1,
        }
    )
)
