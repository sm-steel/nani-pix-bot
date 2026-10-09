"""/history's markdown: the finished-games table and one game's two tabs,
its record and its guess log. Everything that isn't markdown syntax goes
through md_escape — titles, names and guesses are all player- or
provider-written. The record's newer sections (clues, bounty, stages,
participation) are in render_record.py."""

from collections.abc import Sequence
from datetime import timedelta
from zoneinfo import ZoneInfo

from sqlalchemy.orm import Session

from nani_pix_bot.commands.helpers.durations import duration
from nani_pix_bot.commands.helpers.rich import md_escape
from nani_pix_bot.commands.history import render_record
from nani_pix_bot.commands.history.cells import (
    SHORT_TIME,
    as_utc,
    header,
    local,
    name,
    row,
    stage_total,
    t,
)
from nani_pix_bot.models.enums import GameStatus, Provider
from nani_pix_bot.models.game import Game
from nani_pix_bot.models.game_guess import GameGuess
from nani_pix_bot.services import players
from nani_pix_bot.services.game import state
from nani_pix_bot.services.game.history import GameDetail
from nani_pix_bot.services.game.win_facts import stage_number

UNSOLVED_MARK = "❌"


def _when(game: Game):
    return game.ended_at or game.created_at


def list_markdown(
    session: Session, games: Sequence[Game], heading: str, tz: ZoneInfo, lang: str
) -> str:
    lines = ["## " + md_escape(heading), ""]
    lines += header(
        "#",
        t("history.col.date", lang),
        t("history.col.anime", lang),
        t("history.col.winner", lang),
        t("history.col.host", lang),
    )
    for game in games:
        winner = (
            UNSOLVED_MARK if game.status is GameStatus.UNSOLVED else name(session, game.winner_id)
        )
        cells = [
            str(game.id),
            local(_when(game), tz, "%d.%m.%y"),
            state.display_title(game, lang),
            winner,
            name(session, game.starter_id),
        ]
        lines.append(row(cells))
    return "\n".join(lines)


def _other_titles(game: Game, lang: str) -> list[str]:
    shown = state.display_title(game, lang).casefold()
    seen = {shown}
    others = []
    for title in (game.title_russian, game.title_romaji, game.title_english, game.title_native):
        if title and title.casefold() not in seen:
            seen.add(title.casefold())
            others.append(title)
    return others


def _source(game: Game, lang: str) -> list[str]:
    try:
        found = t("history.detail.source", lang, source=Provider(game.source).display_name)
    except ValueError:
        found = t("history.detail.source_manual", lang)
    if game.screenshot_source is None:
        shot = t("history.detail.screenshot_own", lang)
    else:
        provider = Provider(game.screenshot_source).display_name
        shot = t("history.detail.screenshot_provider", lang, provider=provider)
    return [found, shot]


def _timeline(game: Game, tz: ZoneInfo, lang: str) -> list[str]:
    started = game.activated_at or game.created_at
    lines = [t("history.detail.started", lang, when=local(started, tz, "%d.%m.%Y %H:%M"))]
    if game.ended_at is not None:
        ended = local(game.ended_at, tz, "%d.%m.%Y %H:%M")
        lasted = duration(as_utc(game.ended_at) - as_utc(started), lang)
        lines.append(t("history.detail.ended", lang, when=ended, duration=lasted))
    return lines


def _outcome(session: Session, detail: GameDetail, lang: str) -> list[str]:
    game = detail.game
    if game.status is GameStatus.UNSOLVED:
        line = "❌ " + t("history.detail.unsolved", lang)
        if detail.unsolved is not None:
            line += " — " + t(f"history.detail.unsolved.{detail.unsolved.value}", lang)
        return [line]
    key = "history.detail.won_hard" if game.hard_mode else "history.detail.won"
    line = "✅ " + t(
        key,
        lang,
        winner=name(session, game.winner_id),
        stage=stage_number(game),
        total=stage_total(game),
    )
    if detail.how is not None:
        line += " " + t(f"history.detail.how.{detail.how}", lang)
    lines = [line]
    if detail.seconds is not None:
        solved_in = duration(timedelta(seconds=detail.seconds), lang)
        lines.append(t("history.detail.solved_in", lang, duration=solved_in))
    if detail.pot:
        lines.append(t("history.detail.pot", lang, pot=detail.pot))
    if detail.points is not None:
        lines.append(t("history.detail.points", lang, points=detail.points))
    return lines


def _title(game: Game, lang: str) -> str:
    title = state.display_title(game, lang)
    return "## " + t("history.detail.title", lang, id=game.id, title=title)


def record_markdown(session: Session, detail: GameDetail, tz: ZoneInfo, lang: str) -> str:
    """The Record tab: titles, facts, the outcome, then clues, bounty,
    stages and who took part (each only when the game has any)."""
    game = detail.game
    lines = [_title(game, lang)]
    others = _other_titles(game, lang)
    if others:
        lines.append(t("history.detail.also", lang, titles=" / ".join(others)))
    facts = [t("history.detail.host", lang, host=name(session, game.starter_id))]
    facts += _source(game, lang)
    if game.hard_mode:
        facts.append(t("history.detail.hard", lang))
    facts += _timeline(game, tz, lang)
    lines += ["", *(f"- {fact}" for fact in facts), "", *_outcome(session, detail, lang)]
    lines += render_record.sections(session, detail, tz, lang)
    return "\n".join(lines)


def _guess_row(session: Session, guess: GameGuess, game: Game, tz: ZoneInfo) -> str:
    return row(
        [
            local(guess.created_at, tz, SHORT_TIME),
            players.display_name(session, guess.player_id),
            guess.text,
            f"{guess.stage}/{stage_total(game)}",
            "✅" if guess.correct else "❌",
        ]
    )


def guesses_markdown(
    session: Session,
    detail: GameDetail,
    guesses: Sequence[GameGuess],
    tz: ZoneInfo,
    lang: str,
) -> str:
    """The Guesses tab; `guesses` is the page of the guess log to show."""
    game = detail.game
    lines = [_title(game, lang), ""]
    lines.append("### " + t("history.detail.guesses", lang, count=game.total_guess_count))
    if not detail.guesses:
        lines += ["", t("history.detail.no_log", lang)]
        return "\n".join(lines)
    lines += [
        "",
        *header(
            t("history.col.time", lang),
            t("standings.player", lang),
            t("history.col.guess", lang),
            t("history.col.stage", lang),
            "",
        ),
    ]
    lines += [_guess_row(session, guess, game, tz) for guess in guesses]
    return "\n".join(lines)
