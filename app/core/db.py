from collections.abc import Generator

from sqlalchemy import create_engine, inspect, text
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from app.core.config import settings


class Base(DeclarativeBase):
    pass


def _create_engine():
    connect_args = {}
    if settings.database_url.startswith("sqlite:///"):
        connect_args = {"check_same_thread": False}
    return create_engine(
        settings.database_url,
        future=True,
        connect_args=connect_args,
    )


engine = _create_engine()
SessionLocal = sessionmaker(
    bind=engine,
    autoflush=False,
    autocommit=False,
    expire_on_commit=False,
)


def init_db() -> None:
    from app import models  # noqa: F401

    Base.metadata.create_all(bind=engine)
    _apply_runtime_migrations()


def _apply_runtime_migrations() -> None:
    if engine.dialect.name != "sqlite":
        return

    inspector = inspect(engine)
    table_names = set(inspector.get_table_names())
    statements: list[str] = []

    if "mps" in table_names:
        mp_columns = {col["name"] for col in inspector.get_columns("mps")}
        mp_indexes = {idx["name"] for idx in inspector.get_indexes("mps")}

        if "is_favorite" not in mp_columns:
            statements.append(
                "ALTER TABLE mps ADD COLUMN is_favorite BOOLEAN DEFAULT 0"
            )
        if "use_count" not in mp_columns:
            statements.append("ALTER TABLE mps ADD COLUMN use_count INTEGER DEFAULT 0")
        if "last_used_at" not in mp_columns:
            statements.append("ALTER TABLE mps ADD COLUMN last_used_at DATETIME")
        if "ix_mps_is_favorite" not in mp_indexes:
            statements.append(
                "CREATE INDEX IF NOT EXISTS ix_mps_is_favorite ON mps (is_favorite)"
            )

        if "auto_sync_enabled" not in mp_columns:
            statements.append(
                "ALTER TABLE mps ADD COLUMN auto_sync_enabled BOOLEAN DEFAULT 0"
            )
        if "auto_sync_interval_minutes" not in mp_columns:
            statements.append(
                "ALTER TABLE mps ADD COLUMN auto_sync_interval_minutes INTEGER DEFAULT 1440"
            )
        if "auto_sync_lookback_days" not in mp_columns:
            statements.append(
                "ALTER TABLE mps ADD COLUMN auto_sync_lookback_days INTEGER DEFAULT 3"
            )
        if "auto_sync_overlap_hours" not in mp_columns:
            statements.append(
                "ALTER TABLE mps ADD COLUMN auto_sync_overlap_hours INTEGER DEFAULT 6"
            )
        if "auto_sync_next_run_at" not in mp_columns:
            statements.append(
                "ALTER TABLE mps ADD COLUMN auto_sync_next_run_at DATETIME"
            )
        if "auto_sync_last_success_at" not in mp_columns:
            statements.append(
                "ALTER TABLE mps ADD COLUMN auto_sync_last_success_at DATETIME"
            )
        if "auto_sync_last_error" not in mp_columns:
            statements.append("ALTER TABLE mps ADD COLUMN auto_sync_last_error TEXT")
        if "auto_sync_consecutive_failures" not in mp_columns:
            statements.append(
                "ALTER TABLE mps ADD COLUMN auto_sync_consecutive_failures INTEGER DEFAULT 0"
            )

        if "ix_mps_auto_sync_enabled" not in mp_indexes:
            statements.append(
                "CREATE INDEX IF NOT EXISTS ix_mps_auto_sync_enabled ON mps (auto_sync_enabled)"
            )
        if "ix_mps_auto_sync_next_run_at" not in mp_indexes:
            statements.append(
                "CREATE INDEX IF NOT EXISTS ix_mps_auto_sync_next_run_at ON mps (auto_sync_next_run_at)"
            )

    if "capture_jobs" in table_names:
        capture_job_columns = {
            col["name"] for col in inspector.get_columns("capture_jobs")
        }
        capture_job_indexes = {
            idx["name"] for idx in inspector.get_indexes("capture_jobs")
        }

        if "start_ts" not in capture_job_columns:
            statements.append("ALTER TABLE capture_jobs ADD COLUMN start_ts BIGINT")
        if "end_ts" not in capture_job_columns:
            statements.append("ALTER TABLE capture_jobs ADD COLUMN end_ts BIGINT")
        if "source" not in capture_job_columns:
            statements.append(
                "ALTER TABLE capture_jobs ADD COLUMN source VARCHAR(32) DEFAULT 'manual'"
            )
        if "ix_capture_jobs_source" not in capture_job_indexes:
            statements.append(
                "CREATE INDEX IF NOT EXISTS ix_capture_jobs_source ON capture_jobs (source)"
            )

    if not statements:
        return

    with engine.begin() as conn:
        for stmt in statements:
            conn.execute(text(stmt))


def get_db() -> Generator[Session, None, None]:
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
