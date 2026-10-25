"""The look shared by the generated preview cards (the costume sheet, a consumable's
card): a translucent rounded card and a dark name pill, drawn over a background."""

from dataclasses import dataclass

from PIL import Image, ImageDraw

from nani_pix_bot.services.cards.render import font, smooth_mask

CARD_FILL = (255, 255, 255, 26)
CARD_OUTLINE = (255, 255, 255, 60)
PILL_FILL = (18, 12, 34, 210)


def draw_card(canvas: Image.Image, box: tuple[int, int, int, int], radius: int) -> None:
    """A translucent rounded card in `box`, the background showing through."""
    layer = Image.new("RGBA", canvas.size, (0, 0, 0, 0))
    ImageDraw.Draw(layer).rounded_rectangle(
        list(box), radius=radius, fill=CARD_FILL, outline=CARD_OUTLINE, width=2
    )
    canvas.alpha_composite(layer)


@dataclass(frozen=True)
class PillSize:
    height: int
    radius: int
    font_size: int
    padding: int  # added to the text's width


def draw_pill(canvas: Image.Image, text: str, size: PillSize, area: tuple[int, int, int]) -> None:
    """`text` in a pill centred between `area`'s left and right x, its top at `area`'s y."""
    span_left, span_right, top = area
    draw = ImageDraw.Draw(canvas)
    label_font = font(size.font_size, bold=True)
    width = round(draw.textlength(text, font=label_font)) + size.padding
    left = span_left + (span_right - span_left - width) // 2
    mask = smooth_mask(
        (width, size.height),
        lambda shape, k: shape.rounded_rectangle(
            (0, 0, width * k - 1, size.height * k - 1), radius=size.radius * k, fill=255
        ),
    )
    pill = Image.new("RGBA", (width, size.height), (*PILL_FILL[:3], 0))
    pill.putalpha(mask.point(lambda v: v * PILL_FILL[3] // 255))
    canvas.alpha_composite(pill, (left, top))
    draw.text(
        (left + width / 2, top + size.height / 2),
        text,
        font=label_font,
        fill=(255, 255, 255, 255),
        anchor="mm",
    )
