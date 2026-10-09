"""The Record tab's sections after the outcome: clues bought, the bounty
put up, the stage timeline, and who took part. Each is left out when the
game has none of it."""

from zoneinfo import ZoneInfo

from sqlalchemy.orm import Session

from nani_pix_bot.commands.helpers.rich import md_escape
from nani_pix_bot.commands.history.cells import SHORT_TIME, header, local, name, row, stage_total, t
from nani_pix_bot.services import i18n
from nani_pix_bot.services.game.history import ClueRow, GameDetail, StageStep

NOT_SHARED = "—"


def _clue_cells(session: Session, clue: ClueRow, tz: ZoneInfo, lang: str) -> list[str]:
    shared = NOT_SHARED if clue.shared_at is None else local(clue.shared_at, tz, SHORT_TIME)
    return [
        local(clue.at, tz, SHORT_TIME),
        name(session, clue.player_id),
        i18n.t(f"shop.item_name.{clue.kind.value}", lang),
        str(clue.price),
        shared,
    ]


def _clues(session: Session, detail: GameDetail, tz: ZoneInfo, lang: str) -> list[str]:
    if not detail.clues:
        return []
    lines = ["", "### " + t("history.detail.clues", lang, count=len(detail.clues)), ""]
    lines += header(
        t("history.col.time", lang),
        t("standings.player", lang),
        t("history.col.clue", lang),
        t("history.col.price", lang),
        t("history.col.shared", lang),
    )
    return lines + [row(_clue_cells(session, clue, tz, lang)) for clue in detail.clues]


def _bounty(session: Session, detail: GameDetail, lang: str) -> list[str]:
    if not detail.bounty:
        return []
    parts = ", ".join(f"{name(session, pid)} {amount} 💠" for pid, amount in detail.bounty)
    return ["", t("history.detail.bounty", lang, contributions=parts)]


def _stage_cells(step: StageStep, total: int, tz: ZoneInfo, lang: str) -> list[str]:
    return [
        local(step.at, tz, SHORT_TIME),
        f"{step.from_stage} → {step.to_stage}/{total}",
        i18n.t(f"history.detail.stage.{step.reason.value}", lang),
    ]


def _stages(detail: GameDetail, tz: ZoneInfo, lang: str) -> list[str]:
    if not detail.stages:
        return []
    lines = ["", "### " + t("history.detail.stages", lang), ""]
    lines += header(
        t("history.col.time", lang), t("history.col.stage", lang), t("history.col.why", lang)
    )
    total = stage_total(detail.game)
    return lines + [row(_stage_cells(step, total, tz, lang)) for step in detail.stages]


def _people(session: Session, detail: GameDetail, lang: str) -> list[str]:
    lines = []
    if detail.distinct_guessers is not None:
        lines += ["", t("history.detail.guessers", lang, count=detail.distinct_guessers)]
    if detail.votes:
        lines += ["", t("history.detail.votes", lang)]
        lines += [
            f"- {md_escape(name(session, voter))} → {md_escape(name(session, candidate))}"
            for voter, candidate in detail.votes
        ]
    return lines


def sections(session: Session, detail: GameDetail, tz: ZoneInfo, lang: str) -> list[str]:
    return [
        *_clues(session, detail, tz, lang),
        *_bounty(session, detail, lang),
        *_stages(detail, tz, lang),
        *_people(session, detail, lang),
    ]
