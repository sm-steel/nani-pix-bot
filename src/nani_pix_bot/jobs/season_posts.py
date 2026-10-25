"""The season announcements' posts: the teaser/start/end banners, a
player's tier and objective notices (DMs) and the pass-complete line
(seasons spec §2, §5, §6)."""

from collections.abc import Callable
from datetime import datetime
from zoneinfo import ZoneInfo

from loguru import logger
from sqlalchemy.orm import Session

from nani_pix_bot.jobs.post import Post
from nani_pix_bot.models.announcement import AnnouncementOutbox
from nani_pix_bot.models.enums import OutboxKind, SeasonStatus
from nani_pix_bot.models.season import SeasonSchedule
from nani_pix_bot.seasons import registry
from nani_pix_bot.seasons.definition import SeasonRun
from nani_pix_bot.seasons.mechanics import EventDay
from nani_pix_bot.services import i18n, players, settings
from nani_pix_bot.services.cards import SeasonBanner, season_background, season_stamp
from nani_pix_bot.services.cosmetics import flair
from nani_pix_bot.services.events import as_utc
from nani_pix_bot.services.seasons import conversion, objectives, podium_text, season_pass


def _local(at: datetime, tz: ZoneInfo) -> str:
    return as_utc(at).astimezone(tz).strftime("%d.%m %H:%M")


def _season_lines(
    session: Session, kind: OutboxKind, season: SeasonSchedule, run: SeasonRun, lang: str
) -> list[str]:
    tz = settings.get_group_timezone(session)
    when = {"start": _local(season.start_at, tz), "end": _local(season.end_at, tz), "tz": tz.key}
    lines = [
        i18n.t(f"season.post.{kind.value}", lang, name=run.name(lang), emoji=run.emoji, **when)
    ]
    if kind is OutboxKind.SEASON_START and run.gate is not None:
        rule = run.gate.description.get(lang) or run.gate.description["EN"]
        lines.append(i18n.t("season.post.gate", lang, rule=rule))
    if kind is OutboxKind.SEASON_END:
        lines.extend(podium_text.lines(session, season.id, lang))
        done = conversion.summary(session, season.id)
        if done.players:
            lines.append(
                i18n.t(
                    "season.post.conversion",
                    lang,
                    players=done.players,
                    currency=done.currency,
                    rate=run.coins_per_currency,
                )
            )
    return lines


def _open_season(
    session: Session, row: AnnouncementOutbox, kind: OutboxKind
) -> tuple[SeasonSchedule, SeasonRun] | None:
    """The row's season and run, or None (logged) when it's gone, its run
    is gone or the season was cancelled — nothing to announce then."""
    season = session.get(SeasonSchedule, row.payload["season_id"])
    run = registry.get(season.run_id) if season is not None else None
    if season is None or run is None:
        return None
    if season.status == SeasonStatus.CANCELLED:
        logger.info(
            "skipped {kind} for cancelled season {run_id}",
            kind=kind.value,
            run_id=season.run_id,
            season_id=season.id,
        )
        return None
    return season, run


def _season_post(
    kind: OutboxKind,
) -> Callable[[Session, AnnouncementOutbox, str], Post | None]:
    def render(session: Session, row: AnnouncementOutbox, lang: str) -> Post | None:
        found = _open_season(session, row, kind)
        if found is None:
            return None
        season, run = found
        tz = settings.get_group_timezone(session)
        banner = SeasonBanner(
            headline=i18n.t(f"card.season.{kind.value}", lang),
            title=run.name(lang),
            subtitle=f"{_local(season.start_at, tz)} \u2013 {_local(season.end_at, tz)} ({tz.key})",
            stamp=season_stamp(season.run_id, plus=False),
            palette=run.palette,
        )
        return Post(
            (row.id,),
            "\n".join(_season_lines(session, kind, season, run, lang)),
            banner,
            background=season_background(season.run_id),
        )

    return render


def _event_day_lines(
    session: Session, kind: OutboxKind, event: EventDay, run: SeasonRun, lang: str
) -> list[str]:
    name = event.names.get(lang) or event.names["EN"]
    if kind is OutboxKind.EVENT_DAY_END:
        return [i18n.t("event_day.post.end", lang, name=name)]
    tz = settings.get_group_timezone(session)
    lines = [i18n.t("event_day.post.start", lang, name=name, tz=tz.key)]
    if event.all_haunted and run.haunt is not None:
        lines.append(i18n.t("event_day.post.haunted", lang))
    if event.gate is not None:
        rule = event.gate.description.get(lang) or event.gate.description["EN"]
        lines.append(i18n.t("event_day.post.gate", lang, rule=rule))
    if event.coin_multiplier > 1:
        lines.append(i18n.t("event_day.post.coins", lang, multiplier=event.coin_multiplier))
    return lines


def _event_day_post(
    kind: OutboxKind,
) -> Callable[[Session, AnnouncementOutbox, str], Post | None]:
    def render(session: Session, row: AnnouncementOutbox, lang: str) -> Post | None:
        found = _open_season(session, row, kind)
        if found is None:
            return None
        event = found[1].event_day
        if event is None:
            logger.warning(
                "skipped {kind}: run {run_id} has no event day",
                kind=kind.value,
                run_id=found[1].run_id,
                season_id=found[0].id,
            )
            return None
        lines = _event_day_lines(session, kind, event, found[1], lang)
        return Post((row.id,), "\n".join(lines))

    return render


def _run_of(session: Session, row: AnnouncementOutbox) -> SeasonRun | None:
    season = session.get(SeasonSchedule, row.payload["season_id"])
    return registry.get(season.run_id) if season is not None else None


def _tier_post(session: Session, row: AnnouncementOutbox, lang: str) -> Post | None:
    run = _run_of(session, row)
    if run is None:
        return None
    text = season_pass.notice_text(run, row.payload["granted"], lang)
    return Post((row.id,), text, dm_to=row.payload["player_id"])


def _pass_done_post(session: Session, row: AnnouncementOutbox, lang: str) -> Post | None:
    run = _run_of(session, row)
    if run is None:
        return None
    player_id = row.payload["player_id"]
    who = flair.named(session, player_id, players.display_name(session, player_id))
    return Post((row.id,), i18n.t("season_pass.done", lang, player=who, season=run.name(lang)))


def _objective_post(session: Session, row: AnnouncementOutbox, lang: str) -> Post | None:
    run = _run_of(session, row)
    key = row.payload["objective"]
    objective = next((o for o in run.objectives if o.key == key), None) if run else None
    if run is None or objective is None:
        return None
    text = i18n.t(
        "season_objective.notice",
        lang,
        name=objective.name(lang),
        reward=objectives.reward_text(objective, lang),
        season=run.name(lang),
    )
    return Post((row.id,), text, dm_to=row.payload["player_id"])


RENDERERS: dict[OutboxKind, Callable[[Session, AnnouncementOutbox, str], Post | None]] = {
    OutboxKind.SEASON_TEASER: _season_post(OutboxKind.SEASON_TEASER),
    OutboxKind.SEASON_START: _season_post(OutboxKind.SEASON_START),
    OutboxKind.SEASON_END: _season_post(OutboxKind.SEASON_END),
    OutboxKind.EVENT_DAY_START: _event_day_post(OutboxKind.EVENT_DAY_START),
    OutboxKind.EVENT_DAY_END: _event_day_post(OutboxKind.EVENT_DAY_END),
    OutboxKind.SEASON_TIER: _tier_post,
    OutboxKind.SEASON_COMPLETE: _pass_done_post,
    OutboxKind.SEASON_OBJECTIVE: _objective_post,
}
