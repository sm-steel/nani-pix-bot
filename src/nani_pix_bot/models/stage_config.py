from sqlalchemy.orm import Mapped, mapped_column

from nani_pix_bot.models.base import Base
from nani_pix_bot.models.enums import PixelStage


class StageConfig(Base):
    """One row per `PixelStage`, admin-adjustable at runtime — see
    services/settings/stage_config.py and the /stageconfig command family.
    `PixelStage` itself (5 fixed members, their order) is not
    configurable, only each stage's target width and wrong-guess limit."""

    __tablename__ = "stage_config"

    stage: Mapped[PixelStage] = mapped_column(primary_key=True)
    target_width: Mapped[int]
    wrong_guess_limit: Mapped[int]
