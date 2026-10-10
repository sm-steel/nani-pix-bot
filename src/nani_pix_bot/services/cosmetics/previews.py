"""What an item looks like before you equip it (seasons spec §3): a line of text, an image
or a short reveal clip, drawn by the same code that shows the item in a game. Images and
clips are uploaded once; `item_previews` keeps their Telegram file_id."""

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from functools import cache
from io import BytesIO
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
from nani_pix_bot.services import i18n
from nani_pix_bot.services.cards import ProfileCard, render_profile_card
from nani_pix_bot.services.cosmetics import frames, looks
from nani_pix_bot.services.reveal import pipeline
from nani_pix_bot.services.settings.stage_config import DEFAULT_STAGE_CONFIG

SAMPLE_SIZE = (640, 360)
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
    """A made-up scene to show frames and transitions on: a gradient sky, a sun, hills.
    Drawn here (no third-party art), once per process."""
    width, height = SAMPLE_SIZE
    across = Image.linear_gradient("L").rotate(90).resize(SAMPLE_SIZE)
    down = Image.linear_gradient("L").resize(SAMPLE_SIZE)
    image = Image.merge("RGB", (ImageOps.invert(across), down, across))
    draw = ImageDraw.Draw(image)
    draw.ellipse((70, 50, 230, 210), fill=(250, 210, 90))
    draw.polygon([(220, 300), (400, 110), (600, 300)], fill=(60, 110, 90))
    draw.rectangle((0, 280, width, height), fill=(35, 70, 55))
    draw.rectangle((470, 200, 530, 280), fill=(200, 80, 90))
    out = BytesIO()
    image.save(out, format="PNG")
    return out.getvalue()


def _you(lang: str) -> str:
    return i18n.t("cosmetics.preview.you", lang)


@dataclass(frozen=True)
class ClipJob:
    """A reveal clip to render in the worker process: everything in `args()` pickles."""

    effect: RevealEffect
    badge: pipeline.Badge | None

    def args(
        self,
    ) -> tuple[bytes, PixelAlgorithm, tuple[int, ...], RevealEffect, pipeline.Badge | None]:
        return sample_stage(), DEFAULT_ALGORITHM, STAGE_WIDTHS, self.effect, self.badge


def clip_job(resolved: ResolvedItem, lang: str) -> ClipJob | None:
    """The clip a transition or win effect previews as; None for every other slot."""
    item = resolved.item
    if item.slot is CosmeticSlot.HOST_TRANSITION:
        return ClipJob(RevealEffect[item.look], None)
    if item.slot is CosmeticSlot.WIN_EFFECT:
        return ClipJob(RevealEffect.IRIS, pipeline.Badge(None, _you(lang), overlay=item.look))
    return None


def _frame(resolved: ResolvedItem, _lang: str) -> bytes:
    return frames.apply_frame(sample_stage(), resolved.item.look)


def _banner(resolved: ResolvedItem, lang: str) -> bytes:
    card = ProfileCard(
        name=_you(lang),
        title=None,
        stamps=(),
        stats_line=i18n.t("profile.stats", lang, seasons=1, owned=1),
        seed=0,
    )
    background = looks.banner_path(resolved.run.run_id, resolved.item.look).read_bytes()
    return render_profile_card(card, None, background)


_STILLS: Mapping[CosmeticSlot, Callable[[ResolvedItem, str], bytes]] = MappingProxyType(
    {CosmeticSlot.HOST_FRAME: _frame, CosmeticSlot.PROFILE_BANNER: _banner}
)


def _text(resolved: ResolvedItem, lang: str) -> str:
    item = resolved.item
    if item.slot is CosmeticSlot.TITLE:
        return i18n.t("cosmetics.preview.title", lang, title=item.name(lang))
    if item.slot is CosmeticSlot.GUESS_REACTION:
        return i18n.t("cosmetics.preview.reaction", lang, reaction=item.look)
    return f"{_you(lang)} {item.look}"  # name flair, as flair.named shows it


def render(resolved: ResolvedItem, lang: str) -> Preview:
    """The item's preview. Slow for a clip (a whole reveal render): callers run it off the
    event loop."""
    job = clip_job(resolved, lang)
    if job is not None:
        return Preview(PreviewKind.VIDEO, video=pipeline.preview_clip(*job.args()))
    still = _STILLS.get(resolved.item.slot)
    if still is not None:
        return Preview(PreviewKind.PHOTO, image=still(resolved, lang))
    return Preview(PreviewKind.TEXT, text=_text(resolved, lang))


def cached(session: Session, item_id: str) -> ItemPreview | None:
    return session.get(ItemPreview, item_id)


def remember(session: Session, item_id: str, kind: PreviewKind, file_id: str) -> None:
    row = session.get(ItemPreview, item_id)
    if row is None:
        session.add(ItemPreview(item_id=item_id, kind=kind, file_id=file_id))
    else:
        row.kind, row.file_id = kind, file_id
    session.flush()
    logger.debug("cached the {kind} preview of {item_id}", kind=kind.value, item_id=item_id)
