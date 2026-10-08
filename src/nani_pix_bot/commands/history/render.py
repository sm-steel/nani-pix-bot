"""/history's markdown: the finished-games table and one game's record.
Everything that isn't markdown syntax goes through md_escape — titles,
names and guesses are all player- or provider-written."""

from collections.abc import Sequence
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from sqlalchemy.orm import Session

from nani_pix_bot.commands.helpers.durations import duration
from nani_pix_bot.commands.helpers.rich import md_escape
from nani_pix_bot.models.enums import GameStatus, Provider
from nani_pix_bot.models.game import Game
from nani_pix_bot.models.game_guess import GameGuess
from nani_pix_bot.services import i18n, players
from nani_pix_bot.services.achievements import periods
from nani_pix_bot.services.game import state
from nani_pix_bot.services.game.history import GameDetail
from nani_pix_bot.services.game.win_facts import stage_number

UNSOLVED_MARK = "❌"


def _t(key: str, lang: str, **kwargs: object) -> str:
    return md_escape(i18n.t(key, lang, **kwargs))


def _as_utc(at: datetime) -> datetime:
    # DATETIME columns come back naive (UTC) from the DB.
    return at.replace(tzinfo=ZoneInfo("UTC")) if at.tzinfo is None else at


def _local(at: datetime, tz: ZoneInfo, fmt: str) -> str:
    return _as_utc(at).astimezone(tz).strftime(fmt)


def _name(session: Session, player_id: int | None) -> str:
    return "—" if player_id is None else players.display_name(session, player_id)


def _when(game: Game) -> datetime:
    return game.ended_at or game.created_at


def list_markdown(
    session: Session, games: Sequence[Game], heading: str, tz: ZoneInfo, lang: str
) -> str:
    lines = ["## " + md_escape(heading), ""]
    header = [
        "#",
        _t("history.col.date", lang),
        _t("history.col.anime", lang),
        _t("history.col.winner", lang),
        _t("history.col.host", lang),
    ]
    lines += ["| " + " | ".join(header) + " |", "|---|---|---|---|---|"]
    for game in games:
        winner = (
            UNSOLVED_MARK if game.status is GameStatus.UNSOLVED else _name(session, game.winner_id)
        )
        cells = [
            str(game.id),
            _local(_when(game), tz, "%d.%m.%y"),
            state.display_title(game, lang),
            winner,
            _name(session, game.starter_id),
        ]
        lines.append("| " + " | ".join(md_escape(c) for c in cells) + " |")
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
        found = _t("history.detail.source", lang, source=Provider(game.source).display_name)
    except ValueError:
        found = _t("history.detail.source_manual", lang)
    if game.screenshot_source is None:
        shot = _t("history.detail.screenshot_own", lang)
    else:
        provider = Provider(game.screenshot_source).display_name
        shot = _t("history.detail.screenshot_provider", lang, provider=provider)
    return [found, shot]


def _timeline(game: Game, tz: ZoneInfo, lang: str) -> list[str]:
    started = game.activated_at or game.created_at
    lines = [_t("history.detail.started", lang, when=_local(started, tz, "%d.%m.%Y %H:%M"))]
    if game.ended_at is not None:
        ended = _local(game.ended_at, tz, "%d.%m.%Y %H:%M")
        lasted = duration(_as_utc(game.ended_at) - _as_utc(started), lang)
        lines.append(_t("history.detail.ended", lang, when=ended, duration=lasted))
    return lines


def _stage_total(game: Game) -> int:
    return len(periods.HARD_POINTS) if game.hard_mode else len(periods.WIN_POINTS)


def _outcome(session: Session, detail: GameDetail, lang: str) -> list[str]:
    game = detail.game
    if game.status is GameStatus.UNSOLVED:
        line = "❌ " + _t("history.detail.unsolved", lang)
        if detail.unsolved is not None:
            line += " — " + _t(f"history.detail.unsolved.{detail.unsolved.value}", lang)
        return [line]
    key = "history.detail.won_hard" if game.hard_mode else "history.detail.won"
    line = "✅ " + _t(
        key,
        lang,
        winner=_name(session, game.winner_id),
        stage=stage_number(game),
        total=_stage_total(game),
    )
    if detail.how is not None:
        line += " " + _t(f"history.detail.how.{detail.how}", lang)
    lines = [line]
    if detail.seconds is not None:
        solved_in = duration(timedelta(seconds=detail.seconds), lang)
        lines.append(_t("history.detail.solved_in", lang, duration=solved_in))
    if detail.pot:
        lines.append(_t("history.detail.pot", lang, pot=detail.pot))
    if detail.points is not None:
        lines.append(_t("history.detail.points", lang, points=detail.points))
    return lines


def _guess_row(session: Session, guess: GameGuess, game: Game, tz: ZoneInfo) -> str:
    cells = [
        _local(guess.created_at, tz, "%d.%m %H:%M"),
        players.display_name(session, guess.player_id),
        guess.text,
        f"{guess.stage}/{_stage_total(game)}",
        "✅" if guess.correct else "❌",
    ]
    return "| " + " | ".join(md_escape(c) for c in cells) + " |"


def detail_markdown(
    session: Session,
    detail: GameDetail,
    guesses: Sequence[GameGuess],
    tz: ZoneInfo,
    lang: str,
) -> str:
    """The game's record; `guesses` is the page of the guess log to show."""
    game = detail.game
    title = state.display_title(game, lang)
    lines = ["## " + _t("history.detail.title", lang, id=game.id, title=title)]
    others = _other_titles(game, lang)
    if others:
        lines.append(_t("history.detail.also", lang, titles=" / ".join(others)))
    facts = [_t("history.detail.host", lang, host=_name(session, game.starter_id))]
    facts += _source(game, lang)
    if game.hard_mode:
        facts.append(_t("history.detail.hard", lang))
    facts += _timeline(game, tz, lang)
    lines += ["", *(f"- {fact}" for fact in facts), "", *_outcome(session, detail, lang)]
    lines += ["", "### " + _t("history.detail.guesses", lang, count=game.total_guess_count)]
    if not detail.guesses:
        lines += ["", _t("history.detail.no_log", lang)]
        return "\n".join(lines)
    header = [
        _t("history.col.time", lang),
        _t("standings.player", lang),
        _t("history.col.guess", lang),
        _t("history.col.stage", lang),
        "",
    ]
    lines += ["", "| " + " | ".join(header) + " |", "|---|---|---|---|---|"]
    lines += [_guess_row(session, guess, game, tz) for guess in guesses]
    return "\n".join(lines)
