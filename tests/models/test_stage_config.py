from sqlalchemy.orm import Session

from nani_pix_bot.models.enums import PixelStage
from nani_pix_bot.models.stage_config import StageConfig


def test_stage_config_round_trips_width_and_limit(session: Session) -> None:
    session.add(StageConfig(stage=PixelStage.STAGE_3, target_width=100, wrong_guess_limit=2))
    session.commit()
    session.expire_all()

    fetched = session.get(StageConfig, PixelStage.STAGE_3)

    assert fetched is not None
    assert fetched.target_width == 100
    assert fetched.wrong_guess_limit == 2


def test_stage_config_stage_is_the_primary_key(session: Session) -> None:
    session.add(StageConfig(stage=PixelStage.STAGE_1, target_width=64, wrong_guess_limit=1))
    session.commit()

    assert session.get(StageConfig, PixelStage.STAGE_2) is None
