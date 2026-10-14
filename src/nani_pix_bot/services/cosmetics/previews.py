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
from PIL import Image, ImageOps
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
from nani_pix_bot.services.cosmetics import looks, profile, stage
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


# The slots whose preview shows the viewer's avatar.
AVATAR_SLOTS = frozenset({CosmeticSlot.PROFILE_BANNER, CosmeticSlot.WIN_EFFECT})


def cache_key(resolved: ResolvedItem, viewer_id: int) -> str | None:
    """Where the uploaded preview is kept: per viewer for a win effect (their badge), not at
    all for a banner (their live card), else once for everyone."""
    slot = resolved.item.slot
    if slot is CosmeticSlot.PROFILE_BANNER:
        return None
    if slot is CosmeticSlot.WIN_EFFECT:
        return f"{resolved.item_id}@{viewer_id}"
    return resolved.item_id


@dataclass(frozen=True)
class ClipJob:
    """A reveal clip to render in the worker process: everything in `args()` pickles."""

    effect: RevealEffect
    badge: pipeline.Badge | None

    def args(
        self,
    ) -> tuple[bytes, PixelAlgorithm, tuple[int, ...], RevealEffect, pipeline.Badge | None]:
        return sample_stage(), DEFAULT_ALGORITHM, STAGE_WIDTHS, self.effect, self.badge


def clip_job(resolved: ResolvedItem, viewer: Viewer) -> ClipJob | None:
    """The clip a transition or win effect previews as; None for every other slot."""
    item = resolved.item
    if item.slot is CosmeticSlot.HOST_TRANSITION:
        return ClipJob(RevealEffect[item.look], None)
    if item.slot is CosmeticSlot.WIN_EFFECT:
        badge = pipeline.Badge(viewer.avatar, viewer.handle, overlay=item.look)
        return ClipJob(RevealEffect.IRIS, badge)
    return None


def _frame(resolved: ResolvedItem, _lang: str, _viewer: Viewer) -> bytes:
    """Pixelated, in the item's season style, then framed: a real stage post."""
    return stage.stage_png_with(
        sample_stage(), FRAME_STAGE_WIDTH, DEFAULT_ALGORITHM, resolved.item.look, resolved.run.style
    )


def _banner(resolved: ResolvedItem, lang: str, viewer: Viewer) -> bytes:
    card = viewer.card or ProfileCard(
        name=viewer.handle, title=None, stamps=(), stats=profile.stats(0, 0, lang), seed=0
    )
    background = looks.banner_path(resolved.run.run_id, resolved.item.look).read_bytes()
    return render_profile_card(card, viewer.avatar, background)


_STILLS: Mapping[CosmeticSlot, Callable[[ResolvedItem, str, Viewer], bytes]] = MappingProxyType(
    {CosmeticSlot.HOST_FRAME: _frame, CosmeticSlot.PROFILE_BANNER: _banner}
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
