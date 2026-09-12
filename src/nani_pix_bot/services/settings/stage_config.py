"""Per-stage pixelation config (target width + wrong-guess limit),
admin-adjustable at runtime — see commands/stageconfig.py. DB-backed
(the `stage_config` table, one row per `PixelStage`), same
get/set-singleton-ish pattern as this package's bot_settings.py, just
five rows instead of one."""

from dataclasses import dataclass

from loguru import logger
from sqlalchemy import select
from sqlalchemy.orm import Session

from nani_pix_bot.models.enums import PixelStage
from nani_pix_bot.models.stage_config import StageConfig


@dataclass(frozen=True)
class StageSettings:
    target_width: int
    wrong_guess_limit: int


# Fallback only, used for any stage missing a row — normal operation
# always has all 5 seeded by the migration that created this table.
DEFAULT_STAGE_CONFIG: dict[PixelStage, StageSettings] = {
    PixelStage.STAGE_1: StageSettings(target_width=64, wrong_guess_limit=1),
    PixelStage.STAGE_2: StageSettings(target_width=80, wrong_guess_limit=1),
    PixelStage.STAGE_3: StageSettings(target_width=128, wrong_guess_limit=2),
    PixelStage.STAGE_4: StageSettings(target_width=192, wrong_guess_limit=3),
    PixelStage.STAGE_5: StageSettings(target_width=512, wrong_guess_limit=3),
}


def get_stage_config(session: Session) -> dict[PixelStage, StageSettings]:
    rows = {row.stage: row for row in session.scalars(select(StageConfig))}
    return {
        stage: StageSettings(rows[stage].target_width, rows[stage].wrong_guess_limit)
        if stage in rows
        else DEFAULT_STAGE_CONFIG[stage]
        for stage in PixelStage
    }


def set_stage_config(
    session: Session, stage: PixelStage, *, target_width: int, wrong_guess_limit: int
) -> None:
    row = session.get(StageConfig, stage)
    if row is None:
        row = StageConfig(
            stage=stage, target_width=target_width, wrong_guess_limit=wrong_guess_limit
        )
        session.add(row)
    else:
        row.target_width = target_width
        row.wrong_guess_limit = wrong_guess_limit
    logger.info(
        "Stage {} config set to target_width={} wrong_guess_limit={}",
        stage,
        target_width,
        wrong_guess_limit,
    )
