"""Database models and session helpers."""

from datetime import datetime, timezone

from sqlalchemy import DateTime, Float, String, Text, create_engine
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, sessionmaker

from app.config import get_settings


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class Base(DeclarativeBase):
    pass


class ProcessedEmail(Base):
    """One row per Gmail message we have looked at. The primary key is the Gmail message ID,
    which is what prevents duplicate alerts."""

    __tablename__ = "processed_emails"

    gmail_message_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    sender: Mapped[str | None] = mapped_column(String(320))
    subject: Mapped[str | None] = mapped_column(Text)
    # processing | skipped_sender | not_placement | alerted | failed
    status: Mapped[str] = mapped_column(String(32), default="processing")
    category: Mapped[str | None] = mapped_column(String(64))
    confidence: Mapped[float | None] = mapped_column(Float)
    classification_json: Mapped[str | None] = mapped_column(Text)
    whatsapp_message_id: Mapped[str | None] = mapped_column(String(128))
    error: Mapped[str | None] = mapped_column(Text)
    attempts: Mapped[int] = mapped_column(default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)


class SyncState(Base):
    """Key/value state: the last Gmail historyId we processed and the watch expiry."""

    __tablename__ = "sync_state"

    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    value: Mapped[str] = mapped_column(Text)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)


_settings = get_settings()
_connect_args = {"check_same_thread": False} if _settings.database_url.startswith("sqlite") else {}
engine = create_engine(_settings.database_url, pool_pre_ping=True, connect_args=_connect_args)
SessionLocal = sessionmaker(bind=engine, expire_on_commit=False)


def init_db() -> None:
    Base.metadata.create_all(engine)


def get_state(key: str) -> str | None:
    with SessionLocal() as s:
        row = s.get(SyncState, key)
        return row.value if row else None


def set_state(key: str, value: str) -> None:
    with SessionLocal() as s:
        row = s.get(SyncState, key)
        if row:
            row.value = value
        else:
            s.add(SyncState(key=key, value=value))
        s.commit()
