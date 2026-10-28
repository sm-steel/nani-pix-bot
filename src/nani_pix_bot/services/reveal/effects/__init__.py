"""The reveal effects: each renders part 1 of the reveal video (issue #295)."""

from collections.abc import Callable, Mapping
from types import MappingProxyType

from PIL import Image

from nani_pix_bot.models.enums import RevealEffect
from nani_pix_bot.services.reveal import encode
from nani_pix_bot.services.reveal.effects import (
    blossom_branch,
    candle_snuff,
    gashadokuro_rise,
    glitch,
    iris,
    night_parade,
    ripple,
    shatter,
    spring_breeze,
    tile_flip,
)

EFFECTS: Mapping[
    RevealEffect, Callable[[Image.Image, list[Image.Image], encode.Still | None], bytes]
] = MappingProxyType(
    {
        RevealEffect.GLITCH: glitch.render_part1,
        RevealEffect.IRIS: iris.render_part1,
        RevealEffect.RIPPLE: ripple.render_part1,
        RevealEffect.SHATTER: shatter.render_part1,
        RevealEffect.TILE_FLIP: tile_flip.render_part1,
        RevealEffect.SPRING_BREEZE: spring_breeze.render_part1,
        RevealEffect.BLOSSOM_BRANCH: blossom_branch.render_part1,
        RevealEffect.CANDLE_SNUFF: candle_snuff.render_part1,
        RevealEffect.NIGHT_PARADE: night_parade.render_part1,
        RevealEffect.GASHADOKURO_RISE: gashadokuro_rise.render_part1,
    }
)
