from datetime import datetime, timezone

from sqlalchemy import BigInteger, Boolean, DateTime, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class AuthSession(Base):
    __tablename__ = "auth_sessions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, default=1)
    status: Mapped[str] = mapped_column(String(32), default="logged_out", index=True)
    uuid: Mapped[str | None] = mapped_column(String(128), nullable=True)
    token: Mapped[str | None] = mapped_column(String(128), nullable=True)
    fingerprint: Mapped[str | None] = mapped_column(String(128), nullable=True)
    cookie_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    account_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    account_avatar: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=utcnow,
        onupdate=utcnow,
    )


class MPAccount(Base):
    __tablename__ = "mps"

    id: Mapped[str] = mapped_column(String(128), primary_key=True)
    fakeid: Mapped[str] = mapped_column(String(255), unique=True, index=True)
    biz: Mapped[str | None] = mapped_column(String(255), unique=True, nullable=True)
    nickname: Mapped[str] = mapped_column(String(255), index=True)
    alias: Mapped[str | None] = mapped_column(String(255), nullable=True)
    avatar: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    intro: Mapped[str | None] = mapped_column(Text, nullable=True)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    is_favorite: Mapped[bool] = mapped_column(Boolean, default=False, index=True)
    use_count: Mapped[int] = mapped_column(Integer, default=0)
    last_used_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    last_sync_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=utcnow,
        onupdate=utcnow,
    )


class Article(Base):
    __tablename__ = "articles"

    id: Mapped[str] = mapped_column(String(255), primary_key=True)
    aid: Mapped[str | None] = mapped_column(String(255), index=True)
    mp_id: Mapped[str] = mapped_column(String(128), index=True)

    title: Mapped[str] = mapped_column(String(1024), index=True)
    url: Mapped[str] = mapped_column(String(2048), unique=True)
    cover_url: Mapped[str | None] = mapped_column(String(2048), nullable=True)
    digest: Mapped[str | None] = mapped_column(Text, nullable=True)
    author: Mapped[str | None] = mapped_column(String(255), nullable=True)

    publish_ts: Mapped[int | None] = mapped_column(
        BigInteger, index=True, nullable=True
    )
    content_html: Mapped[str | None] = mapped_column(Text, nullable=True)
    content_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    images_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    raw_json: Mapped[str | None] = mapped_column(Text, nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=utcnow,
        onupdate=utcnow,
    )


class CaptureJob(Base):
    __tablename__ = "capture_jobs"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    mp_id: Mapped[str] = mapped_column(String(128), index=True)
    mp_nickname: Mapped[str] = mapped_column(String(255))

    status: Mapped[str] = mapped_column(String(32), default="queued", index=True)
    pages_hint: Mapped[int] = mapped_column(Integer, default=1)
    requested_count: Mapped[int] = mapped_column(Integer, default=20)
    start_ts: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    end_ts: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    fetch_content: Mapped[bool] = mapped_column(Boolean, default=True)

    created_count: Mapped[int] = mapped_column(Integer, default=0)
    updated_count: Mapped[int] = mapped_column(Integer, default=0)
    content_updated_count: Mapped[int] = mapped_column(Integer, default=0)
    duplicates_skipped: Mapped[int] = mapped_column(Integer, default=0)
    scanned_pages: Mapped[int] = mapped_column(Integer, default=0)
    max_pages: Mapped[int] = mapped_column(Integer, default=0)
    reached_target: Mapped[bool] = mapped_column(Boolean, default=False)

    result_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow
    )
    started_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    finished_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=utcnow,
        onupdate=utcnow,
    )
