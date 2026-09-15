import pytest
from sqlalchemy.orm import Session

from nani_pix_bot.models.enums import PixelStage
from nani_pix_bot.models.stage_config import StageConfig
from nani_pix_bot.services.settings import stage_config


def test_get_stage_configs_falls_back_to_defaults_when_no_rows_exist(session: Session) -> None:
    config = stage_config.get_stage_configs(session)

    assert set(config) == set(PixelStage)
    assert config[PixelStage.STAGE_1].target_width == 64
    assert config[PixelStage.STAGE_1].wrong_guess_limit == 1
    assert config[PixelStage.STAGE_5].target_width == 512
    assert config[PixelStage.STAGE_5].wrong_guess_limit == 3


def test_get_stage_configs_returns_stored_values_and_falls_back_for_the_rest(
    session: Session,
) -> None:
    session.add(StageConfig(stage=PixelStage.STAGE_3, target_width=100, wrong_guess_limit=2))
    session.commit()

    config = stage_config.get_stage_configs(session)

    assert config[PixelStage.STAGE_3].target_width == 100
    assert config[PixelStage.STAGE_3].wrong_guess_limit == 2
    # STAGE_1 has no row — falls back to the default.
    assert config[PixelStage.STAGE_1].target_width == 64


def test_get_stage_config_falls_back_to_default_when_no_row_exists(session: Session) -> None:
    settings = stage_config.get_stage_config(session, PixelStage.STAGE_1)

    assert settings.target_width == 64
    assert settings.wrong_guess_limit == 1


def test_get_stage_config_returns_the_stored_row_for_that_stage_only(session: Session) -> None:
    session.add(StageConfig(stage=PixelStage.STAGE_3, target_width=100, wrong_guess_limit=2))
    session.commit()

    settings = stage_config.get_stage_config(session, PixelStage.STAGE_3)

    assert settings.target_width == 100
    assert settings.wrong_guess_limit == 2


def test_get_stage_config_does_not_touch_other_stages_rows(
    session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A real targeted fetch, not a bulk-fetch-then-index — proven here by
    asserting the bulk-query path (`session.scalars(select(StageConfig))`,
    what `get_stage_configs` uses) is never invoked when asking for a
    single stage; `get_stage_config` must go through `session.get` instead."""
    session.add(StageConfig(stage=PixelStage.STAGE_1, target_width=64, wrong_guess_limit=1))
    session.add(StageConfig(stage=PixelStage.STAGE_3, target_width=100, wrong_guess_limit=2))
    session.commit()

    def _guard(*args, **kwargs):
        msg = "get_stage_config must not run the bulk select(StageConfig) query"
        raise AssertionError(msg)

    monkeypatch.setattr(session, "scalars", _guard)

    settings = stage_config.get_stage_config(session, PixelStage.STAGE_3)

    assert settings.target_width == 100


def test_set_stage_config_creates_the_row_if_missing(session: Session) -> None:
    stage_config.set_stage_config(session, PixelStage.STAGE_2, target_width=90, wrong_guess_limit=2)
    session.commit()

    fetched = session.get(StageConfig, PixelStage.STAGE_2)
    assert fetched is not None
    assert fetched.target_width == 90
    assert fetched.wrong_guess_limit == 2


def test_set_stage_config_updates_an_existing_row(session: Session) -> None:
    session.add(StageConfig(stage=PixelStage.STAGE_1, target_width=64, wrong_guess_limit=1))
    session.commit()

    stage_config.set_stage_config(session, PixelStage.STAGE_1, target_width=70, wrong_guess_limit=2)
    session.commit()

    fetched = session.get(StageConfig, PixelStage.STAGE_1)
    assert fetched is not None
    assert fetched.target_width == 70
    assert fetched.wrong_guess_limit == 2
