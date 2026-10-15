# ai.disclaimers: versioned warning texts. Each assessment stores the disclaimer_id it showed.

from datetime import datetime
from typing import TYPE_CHECKING, List, Optional

from sqlalchemy import Integer, Text, func
from sqlalchemy import text as sql  # "text" is also a column name below
from sqlalchemy.orm import Mapped, mapped_column, relationship

from collection.base import TIMESTAMPTZ, Base

if TYPE_CHECKING:
    from collection.assessment import Assessment


class Disclaimer(Base):
    __tablename__ = "disclaimers"
    __table_args__ = {
        "comment": "Versioned disclaimer texts. Each result stores the disclaimer_id shown (Spec, FDA language layer).",
    }

    id: Mapped[str] = mapped_column(Text, primary_key=True)
    version: Mapped[int] = mapped_column(Integer)
    language: Mapped[str] = mapped_column(Text, server_default=sql("'en'"))
    text: Mapped[str] = mapped_column(Text)
    active_from: Mapped[datetime] = mapped_column(TIMESTAMPTZ, server_default=func.now())
    active_to: Mapped[Optional[datetime]] = mapped_column(TIMESTAMPTZ, comment="Empty = the current version.")
    created_at: Mapped[datetime] = mapped_column(TIMESTAMPTZ, server_default=func.now())

    assessments: Mapped[List["Assessment"]] = relationship(back_populates="disclaimer")
