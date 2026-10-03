from sqlalchemy import String
from sqlalchemy.orm import Mapped, mapped_column

from nani_pix_bot.models.base import Base

KEY_LENGTH = 64


class PixelConfig(Base):
    """Admin overrides for pixel amounts (/pixelconfig). Only changed
    values have a row — defaults are named constants in
    services/economy/config.py, so adding a new amount needs no seeding
    migration."""

    __tablename__ = "pixel_config"

    key: Mapped[str] = mapped_column(String(KEY_LENGTH), primary_key=True)
    value: Mapped[int]
