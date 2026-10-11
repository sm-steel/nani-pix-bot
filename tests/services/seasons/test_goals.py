from datetime import UTC, datetime

from nani_pix_bot.models.enums import EventType
from nani_pix_bot.seasons.definition import Goal, GoalKind
from nani_pix_bot.services.seasons import goals
from tests.services.achievements.fake_history import FakeHistory, ev

E = EventType


def _history(*events) -> FakeHistory:
    return FakeHistory(1, list(events))


def test_hosted_ignores_hard_mode() -> None:
    history = _history(
        ev(E.GAME_ACTIVATED, 1, hard_mode=False),
        ev(E.GAME_ACTIVATED, 1, hard_mode=True),
        ev(E.GAME_ACTIVATED, 2, hard_mode=False),
    )
    assert goals.progress(Goal(GoalKind.HOSTED, 2), history) == 1


def test_wins_with_a_stage_limit() -> None:
    history = _history(
        ev(E.GAME_WON, 1, stage=1, hard_mode=False),
        ev(E.GAME_WON, 1, stage=4, hard_mode=False),
        ev(E.GAME_WON, 1, stage=1, hard_mode=True),
        ev(E.GAME_WON, 1, stage=0, hard_mode=False),
    )
    assert goals.progress(Goal(GoalKind.WINS, 1, max_stage=2), history) == 1
    assert goals.progress(Goal(GoalKind.WINS, 3), history) == 4


def test_guessed_in_counts_games_not_guesses() -> None:
    history = _history(ev(E.GUESS, 1, game=1), ev(E.GUESS, 1, game=1), ev(E.GUESS, 1, game=2))
    assert goals.progress(Goal(GoalKind.GUESSED_IN, 5), history) == 2


def test_tips_only() -> None:
    history = _history(ev(E.CURRENCY_MOVED, 1, reason="tip"), ev(E.CURRENCY_MOVED, 1, reason="win"))
    assert goals.progress(Goal(GoalKind.TIPS, 1), history) == 1


def test_shared_clues_and_days() -> None:
    history = _history(
        ev(E.CLUE_SHARED, 1),
        ev(E.GUESS, 1, at=datetime(2026, 11, 1, 9, tzinfo=UTC)),
        ev(E.GUESS, 1, at=datetime(2026, 11, 1, 10, tzinfo=UTC)),
        ev(E.GAME_ACTIVATED, 1, hard_mode=False, at=datetime(2026, 11, 2, 9, tzinfo=UTC)),
    )
    assert goals.progress(Goal(GoalKind.SHARED_CLUES, 1), history) == 1
    assert goals.progress(Goal(GoalKind.DAYS_PLAYED, 4), history) == 2


def test_every_kind_has_triggers_and_a_description() -> None:
    for kind in GoalKind:
        assert goals.TRIGGERS[kind]
        for lang in ("EN", "RU"):
            stage = 2 if kind is GoalKind.WINS else None
            text = goals.describe(Goal(kind, 3, max_stage=stage), lang)
            assert "3" in text
            assert "season_objective" not in text


def test_a_stage_limit_is_described() -> None:
    assert "2" in goals.describe(Goal(GoalKind.WINS, 3, max_stage=2), "EN")


def test_descriptions_agree_with_the_count() -> None:
    hosted = [goals.describe(Goal(GoalKind.HOSTED, n), "RU") for n in (1, 2, 5)]
    assert hosted == ["Загадайте 1 игру", "Загадайте 2 игры", "Загадайте 5 игр"]
    by_stage = [goals.describe(Goal(GoalKind.WINS, n, max_stage=2), "EN") for n in (1, 2, 5)]
    assert by_stage == [
        "Win 1 game by stage 2",
        "Win 2 games by stage 2",
        "Win 5 games by stage 2",
    ]
    ru_by_stage = [goals.describe(Goal(GoalKind.WINS, n, max_stage=2), "RU") for n in (1, 2, 5)]
    wins = ["Победите в 1 игре", "Победите в 2 играх", "Победите в 5 играх"]
    assert [text.split(" не позже ")[0] for text in ru_by_stage] == wins
    assert all(
        text.endswith("этапа") and "2" in text.split(" не позже ")[1] for text in ru_by_stage
    )
    tips = [goals.describe(Goal(GoalKind.TIPS, n), "EN") for n in (1, 2, 5)]
    assert tips == ["Send 1 tip", "Send 2 tips", "Send 5 tips"]
    days = [goals.describe(Goal(GoalKind.DAYS_PLAYED, n), "RU") for n in (1, 2, 5)]
    assert days == ["Играйте 1 день", "Играйте 2 разных дня", "Играйте 5 разных дней"]
