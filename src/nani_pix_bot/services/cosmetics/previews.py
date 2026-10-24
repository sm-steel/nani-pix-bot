"""What an item looks like before you equip it (seasons spec §3): a line of text, an image
or a short reveal clip, drawn by the same code that shows the item in a game, on the
viewer themselves (#427): their name with a flair, their avatar and name on a win badge,
their own profile card under a banner. Images and clips are uploaded once; `item_previews`
keeps their Telegram file_id per language under `cache_key` — per viewer where the viewer
shows, never for a banner (their card's stats move on)."""

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from functools import cache
from io import BytesIO
from pathlib import Path
from types import MappingProxyType

from loguru import logger
from PIL import Image, ImageDraw, ImageOps
from sqlalchemy.orm import Session

from nani_pix_bot.models.cosmetics import ItemPreview
from nani_pix_bot.models.enums import (
    DEFAULT_ALGORITHM,
    CosmeticSlot,
    PixelAlgorithm,
    PixelStage,
    PreviewKind,
    RevealEffect,
)
from nani_pix_bot.seasons.registry import ResolvedItem
from nani_pix_bot.services import i18n, mascot
from nani_pix_bot.services.cards import ProfileCard, render_profile_card
from nani_pix_bot.services.cards.render import font
from nani_pix_bot.services.cosmetics import looks, profile, stage
from nani_pix_bot.services.mascot import Pose
from nani_pix_bot.services.reveal import pipeline
from nani_pix_bot.services.settings.stage_config import DEFAULT_STAGE_CONFIG

SAMPLE_SIZE = (640, 360)
# A made-for-the-bot anime still, season-neutral, original characters (#424).
SAMPLE_SCREENSHOT = Path(__file__).resolve().parents[2] / "assets" / "sample_screenshot.jpg"
# A frame is shown on the stage 3 picture, as a real game posts it mid-round.
FRAME_STAGE_WIDTH = DEFAULT_STAGE_CONFIG[PixelStage.STAGE_3].target_width
# The defaults, not the admin's live stage config: a preview shows the look, not this group.
STAGE_WIDTHS = tuple(DEFAULT_STAGE_CONFIG[stage].target_width for stage in PixelStage)


@dataclass(frozen=True)
class Preview:
    kind: PreviewKind
    text: str | None = None
    image: bytes | None = None
    video: bytes | None = None


@cache
def sample_stage() -> bytes:
    """The neutral sample anime screenshot (#424) fitted to SAMPLE_SIZE, as a PNG: what
    frames and transitions are shown on. Once per process."""
    out = BytesIO()
    with Image.open(SAMPLE_SCREENSHOT) as source:
        ImageOps.fit(source.convert("RGB"), SAMPLE_SIZE).save(out, format="PNG")
    return out.getvalue()


@dataclass(frozen=True)
class Viewer:
    """Who the preview is for, named as a game names them: `handle` on a win badge
    (players.badge_handle), `full_name` before a flair (flair.named). `avatar` (None:
    initials) and `card` (their profile card as /profile draws it) are only filled in for
    the slots that show them."""

    player_id: int
    handle: str
    full_name: str
    avatar: bytes | None = None
    card: ProfileCard | None = None
    costume: str | None = None  # the costume Nani wears on their previews (None: plain)


# The slots whose preview shows the viewer's avatar.
AVATAR_SLOTS = frozenset({CosmeticSlot.PROFILE_BANNER, CosmeticSlot.WIN_EFFECT})


# Appended to the cache key of the previews that show Nani Pix (issue #461), so a copy
# uploaded before she was added is never served again.
WITH_NANI = "#nani"
_NANI_SLOTS = frozenset(
    {CosmeticSlot.HOST_FRAME, CosmeticSlot.HOST_TRANSITION, CosmeticSlot.WIN_EFFECT}
)


def cache_key(
    resolved: ResolvedItem, viewer_id: int, costume: str | None = None, lang: str = "EN"
) -> str | None:
    """Where the uploaded preview is kept: per viewer for a win effect (their badge), not at
    all for a banner (their live card), per language for a costume's sheet (it names the
    poses), else once for everyone. A preview that shows Nani also carries the costume she
    wears in it; a plain one's key is what it always was."""
    slot = resolved.item.slot
    if slot is CosmeticSlot.PROFILE_BANNER:
        return None
    if slot is CosmeticSlot.MASCOT_COSTUME:
        return f"{resolved.item_id}#{lang.lower()}"
    key = f"{resolved.item_id}@{viewer_id}" if slot is CosmeticSlot.WIN_EFFECT else resolved.item_id
    if slot not in _NANI_SLOTS:
        return key
    return key + WITH_NANI + (f"@{costume}" if costume else "")


@dataclass(frozen=True)
class ClipJob:
    """A reveal clip to render in the worker process: everything in `args()` pickles."""

    effect: RevealEffect
    badge: pipeline.Badge | None
    costume: str | None = None

    def ending(self) -> pipeline.Ending:
        """Like a won game's: the badge (if any), Nani in the win pose and her costume."""
        return pipeline.Ending(self.badge, pipeline.Nani(Pose.WIN, self.costume))

    def args(self) -> tuple[bytes, PixelAlgorithm, tuple[int, ...], RevealEffect, pipeline.Ending]:
        return sample_stage(), DEFAULT_ALGORITHM, STAGE_WIDTHS, self.effect, self.ending()


def clip_job(resolved: ResolvedItem, viewer: Viewer) -> ClipJob | None:
    """The clip a transition or win effect previews as; None for every other slot."""
    item = resolved.item
    if item.slot is CosmeticSlot.HOST_TRANSITION:
        return ClipJob(RevealEffect[item.look], None, viewer.costume)
    if item.slot is CosmeticSlot.WIN_EFFECT:
        badge = pipeline.Badge(viewer.avatar, viewer.handle, overlay=item.look)
        return ClipJob(RevealEffect.IRIS, badge, viewer.costume)
    return None


def _frame(resolved: ResolvedItem, _lang: str, viewer: Viewer) -> bytes:
    """Pixelated, in the item's season style, then framed, with Nani in the viewer's
    costume: a real stage-3 post."""
    framed = stage.stage_png_with(
        sample_stage(), FRAME_STAGE_WIDTH, DEFAULT_ALGORITHM, resolved.item.look, resolved.run.style
    )
    return mascot.add_to_png(framed, Pose.NOPE, viewer.costume)  # FRAME_STAGE_WIDTH is stage 3's


def _banner(resolved: ResolvedItem, lang: str, viewer: Viewer) -> bytes:
    card = viewer.card or ProfileCard(
        name=viewer.handle, title=None, stamps=(), stats=profile.stats(0, 0, lang), seed=0
    )
    background = looks.banner_path(resolved.run.run_id, resolved.item.look).read_bytes()
    return render_profile_card(card, viewer.avatar, background)


SHEET_BACKGROUND = Path(__file__).resolve().parents[2] / "assets" / "costume_sheet_background.jpg"
SHEET_COLUMNS = 3
SHEET_CELL = (240, 280)
SHEET_GAP = 18
SHEET_MARGIN = 28
SHEET_POSE_WIDTH = 216
SHEET_LABEL_HEIGHT = 44
_CARD_RADIUS = 20
_CARD_FILL = (255, 255, 255, 26)
_CARD_OUTLINE = (255, 255, 255, 60)
_PILL_RADIUS = 16
_PILL_HEIGHT = 32
_PILL_PADDING = 32
_PILL_FILL = (18, 12, 34, 210)
_LABEL_FONT_SIZE = 22


def _sheet_size() -> tuple[int, int]:
    cell_w, cell_h = SHEET_CELL
    rows = -(-len(Pose) // SHEET_COLUMNS)
    return (
        2 * SHEET_MARGIN + SHEET_COLUMNS * cell_w + (SHEET_COLUMNS - 1) * SHEET_GAP,
        2 * SHEET_MARGIN + rows * cell_h + (rows - 1) * SHEET_GAP,
    )


@cache
def _sheet_background() -> Image.Image:
    """The night picture fitted to the sheet, decoded once per process."""
    with Image.open(SHEET_BACKGROUND) as source:
        return ImageOps.fit(source.convert("RGBA"), _sheet_size(), Image.Resampling.LANCZOS)


def _draw_card(sheet: Image.Image, origin: tuple[int, int]) -> None:
    """A translucent rounded card at `origin`, the background showing through."""
    layer = Image.new("RGBA", sheet.size, (0, 0, 0, 0))
    x, y = origin
    ImageDraw.Draw(layer).rounded_rectangle(
        [x, y, x + SHEET_CELL[0], y + SHEET_CELL[1]],
        radius=_CARD_RADIUS,
        fill=_CARD_FILL,
        outline=_CARD_OUTLINE,
        width=2,
    )
    sheet.alpha_composite(layer)


def _draw_label(sheet: Image.Image, origin: tuple[int, int], text: str) -> None:
    """The pose's name in a pill under its figure."""
    cell_w, cell_h = SHEET_CELL
    draw = ImageDraw.Draw(sheet)
    label_font = font(_LABEL_FONT_SIZE, bold=True)
    width = round(draw.textlength(text, font=label_font)) + _PILL_PADDING
    left = origin[0] + (cell_w - width) // 2
    top = origin[1] + cell_h - SHEET_LABEL_HEIGHT + 4
    pill = Image.new("RGBA", sheet.size, (0, 0, 0, 0))
    ImageDraw.Draw(pill).rounded_rectangle(
        [left, top, left + width, top + _PILL_HEIGHT], radius=_PILL_RADIUS, fill=_PILL_FILL
    )
    sheet.alpha_composite(pill)
    draw.text(
        (left + width / 2, top + _PILL_HEIGHT / 2),
        text,
        font=label_font,
        fill=(255, 255, 255, 255),
        anchor="mm",
    )


def costume_sheet(costume: str, lang: str) -> bytes:
    """A PNG of all six poses in `costume` on a night background: a card per pose, in Pose
    order three to a row, every figure standing on one line above its name in `lang`."""
    sheet = _sheet_background().copy()
    cell_w, cell_h = SHEET_CELL
    for index, pose in enumerate(Pose):
        col, row = index % SHEET_COLUMNS, index // SHEET_COLUMNS
        origin = (
            SHEET_MARGIN + col * (cell_w + SHEET_GAP),
            SHEET_MARGIN + row * (cell_h + SHEET_GAP),
        )
        _draw_card(sheet, origin)
        figure = mascot.sized(pose, SHEET_POSE_WIDTH, costume)
        bottom = origin[1] + cell_h - SHEET_LABEL_HEIGHT - 4
        sheet.alpha_composite(
            figure, (origin[0] + (cell_w - figure.width) // 2, bottom - figure.height)
        )
        _draw_label(sheet, origin, i18n.t(f"mascot.pose.{pose.value}", lang))
    out = BytesIO()
    sheet.convert("RGB").save(out, format="PNG")
    return out.getvalue()


def _costume_sheet(resolved: ResolvedItem, lang: str, _viewer: Viewer) -> bytes:
    return costume_sheet(resolved.item.look, lang)


_STILLS: Mapping[CosmeticSlot, Callable[[ResolvedItem, str, Viewer], bytes]] = MappingProxyType(
    {
        CosmeticSlot.HOST_FRAME: _frame,
        CosmeticSlot.PROFILE_BANNER: _banner,
        CosmeticSlot.MASCOT_COSTUME: _costume_sheet,
    }
)


def _text(resolved: ResolvedItem, lang: str, viewer: Viewer) -> str:
    item = resolved.item
    if item.slot is CosmeticSlot.TITLE:
        return i18n.t("cosmetics.preview.title", lang, title=item.name(lang))
    if item.slot is CosmeticSlot.GUESS_REACTION:
        return i18n.t("cosmetics.preview.reaction", lang, reaction=item.look)
    return f"{viewer.full_name} {item.look}"  # name flair, as flair.named shows it


def render(resolved: ResolvedItem, lang: str, viewer: Viewer) -> Preview:
    """The item's preview for `viewer`. Slow for a clip (a whole reveal render): callers run
    it off the event loop."""
    job = clip_job(resolved, viewer)
    if job is not None:
        return Preview(PreviewKind.VIDEO, video=pipeline.preview_clip(*job.args()))
    still = _STILLS.get(resolved.item.slot)
    if still is not None:
        return Preview(PreviewKind.PHOTO, image=still(resolved, lang, viewer))
    return Preview(PreviewKind.TEXT, text=_text(resolved, lang, viewer))


def cached(session: Session, key: str, lang: str) -> ItemPreview | None:
    return session.get(ItemPreview, (key, lang))


def forget(session: Session, key: str, lang: str) -> None:
    """Drops a cached preview Telegram no longer accepts."""
    row = session.get(ItemPreview, (key, lang))
    if row is not None:
        session.delete(row)
        session.flush()


def remember(session: Session, item_id: str, lang: str, kind: PreviewKind, file_id: str) -> None:
    row = session.get(ItemPreview, (item_id, lang))
    if row is None:
        session.add(ItemPreview(item_id=item_id, lang=lang, kind=kind, file_id=file_id))
    else:
        row.kind, row.file_id = kind, file_id
    session.flush()
    logger.debug("cached the {kind} preview of {item_id}", kind=kind.value, item_id=item_id)
