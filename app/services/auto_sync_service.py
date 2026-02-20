import logging
import random
import threading
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import or_
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.db import SessionLocal
from app.models import MPAccount
from app.services.article_service import article_service
from app.services.capture_job_service import capture_job_service
from app.services.wechat_client import WeChatAuthError, wechat_client


logger = logging.getLogger(__name__)


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class AutoSyncService:
    def __init__(self) -> None:
        self._thread: threading.Thread | None = None
        self._stop_event = threading.Event()
        self._state_lock = threading.Lock()
        self._enabled = bool(settings.auto_sync_enabled)

    def start(self) -> None:
        with self._state_lock:
            if self._thread and self._thread.is_alive():
                return
            self._stop_event.clear()
            self._thread = threading.Thread(
                target=self._run_loop,
                daemon=True,
                name="auto-sync-scheduler",
            )
            self._thread.start()

        db = SessionLocal()
        try:
            self.sync_favorite_targets(db, run_immediately=False)
        except Exception:  # noqa: BLE001
            logger.exception("sync favorite targets failed at startup")
        finally:
            db.close()

        if not self._enabled:
            logger.info("auto sync scheduler started in disabled state")

    def stop(self) -> None:
        self._stop_event.set()
        thread = self._thread
        if thread and thread.is_alive():
            thread.join(timeout=3)

    def is_running(self) -> bool:
        thread = self._thread
        return bool(thread and thread.is_alive() and not self._stop_event.is_set())

    def is_enabled(self) -> bool:
        with self._state_lock:
            return bool(self._enabled)

    def set_enabled(self, enabled: bool) -> bool:
        should_start = False
        with self._state_lock:
            self._enabled = bool(enabled)
            should_start = self._enabled and not (
                self._thread and self._thread.is_alive()
            )

        if should_start:
            self.start()
        return self.is_enabled()

    def sync_favorite_targets(
        self,
        db: Session,
        *,
        run_immediately: bool = False,
    ) -> dict[str, int]:
        now = utcnow()
        rows = db.query(MPAccount).filter(MPAccount.enabled.is_(True)).all()

        changed_count = 0
        enabled_count = 0
        for row in rows:
            should_enable = bool(row.is_favorite)
            row_changed = False

            if should_enable:
                enabled_count += 1

            if bool(row.auto_sync_enabled) != should_enable:
                row.auto_sync_enabled = should_enable
                row_changed = True

            row.auto_sync_interval_minutes = (
                article_service.normalize_auto_sync_interval_minutes(
                    row.auto_sync_interval_minutes
                )
            )
            row.auto_sync_lookback_days = (
                article_service.normalize_auto_sync_lookback_days(
                    row.auto_sync_lookback_days
                )
            )
            row.auto_sync_overlap_hours = (
                article_service.normalize_auto_sync_overlap_hours(
                    row.auto_sync_overlap_hours
                )
            )

            if should_enable:
                if run_immediately:
                    if row.auto_sync_next_run_at != now:
                        row.auto_sync_next_run_at = now
                        row_changed = True
                elif row.auto_sync_next_run_at is None:
                    row.auto_sync_next_run_at = now
                    row_changed = True
            else:
                if row.auto_sync_next_run_at is not None:
                    row.auto_sync_next_run_at = None
                    row_changed = True
                if row.auto_sync_last_error:
                    row.auto_sync_last_error = None
                    row_changed = True
                if int(row.auto_sync_consecutive_failures or 0) != 0:
                    row.auto_sync_consecutive_failures = 0
                    row_changed = True

            if row_changed:
                row.updated_at = now
                db.add(row)
                changed_count += 1

        if changed_count > 0:
            db.commit()

        return {
            "changed": changed_count,
            "enabled": enabled_count,
        }

    def _run_loop(self) -> None:
        tick_seconds = max(10, int(settings.auto_sync_tick_seconds or 45))
        while not self._stop_event.wait(tick_seconds):
            if not self.is_enabled():
                continue
            try:
                self._run_once()
            except Exception:  # noqa: BLE001
                logger.exception("auto sync scheduler loop failed")

    @staticmethod
    def _due_query(db: Session, now: datetime):
        return (
            db.query(MPAccount)
            .filter(
                MPAccount.enabled.is_(True),
                MPAccount.auto_sync_enabled.is_(True),
                or_(
                    MPAccount.auto_sync_next_run_at.is_(None),
                    MPAccount.auto_sync_next_run_at <= now,
                ),
            )
            .order_by(
                MPAccount.auto_sync_next_run_at.is_(None).desc(),
                MPAccount.auto_sync_next_run_at.asc(),
                MPAccount.updated_at.asc(),
            )
        )

    def _pick_due_mp(self, db: Session, now: datetime) -> MPAccount | None:
        limit = max(1, int(settings.auto_sync_scan_limit or 10))
        rows = self._due_query(db, now).limit(limit).all()
        if not rows:
            return None
        return rows[0]

    def _compute_next_run_at(
        self, *, base_time: datetime, interval_minutes: int
    ) -> datetime:
        jitter_max_seconds = max(
            0, int(settings.auto_sync_dispatch_jitter_seconds or 0)
        )
        jitter_seconds = (
            random.randint(0, jitter_max_seconds) if jitter_max_seconds > 0 else 0
        )
        return article_service.compute_next_auto_sync_run(
            base_time=base_time,
            interval_minutes=interval_minutes,
            jitter_seconds=jitter_seconds,
        )

    def _build_capture_range(self, mp: MPAccount, now: datetime) -> tuple[int, int]:
        end_ts = int(now.timestamp())
        lookback_days = article_service.normalize_auto_sync_lookback_days(
            mp.auto_sync_lookback_days
        )
        overlap_hours = article_service.normalize_auto_sync_overlap_hours(
            mp.auto_sync_overlap_hours
        )

        fallback_start_ts = max(0, end_ts - lookback_days * 86400)
        if mp.auto_sync_last_success_at:
            last_success_ts = int(mp.auto_sync_last_success_at.timestamp())
            overlap_start_ts = max(0, last_success_ts - overlap_hours * 3600)
            start_ts = max(fallback_start_ts, overlap_start_ts)
        else:
            start_ts = fallback_start_ts

        if start_ts > end_ts:
            start_ts = end_ts
        return start_ts, end_ts

    def _mark_dispatch_failure(
        self,
        db: Session,
        mp: MPAccount,
        *,
        error: str,
    ) -> None:
        now = utcnow()
        mp.auto_sync_interval_minutes = (
            article_service.normalize_auto_sync_interval_minutes(
                mp.auto_sync_interval_minutes
            )
        )
        mp.auto_sync_lookback_days = article_service.normalize_auto_sync_lookback_days(
            mp.auto_sync_lookback_days
        )
        mp.auto_sync_overlap_hours = article_service.normalize_auto_sync_overlap_hours(
            mp.auto_sync_overlap_hours
        )

        failures = max(0, int(mp.auto_sync_consecutive_failures or 0)) + 1
        mp.auto_sync_consecutive_failures = failures
        mp.auto_sync_last_error = (error or "自动同步调度失败").strip()[:1000]

        base_backoff = max(
            1, int(settings.auto_sync_failure_backoff_base_minutes or 15)
        )
        max_backoff = max(
            base_backoff,
            int(settings.auto_sync_failure_backoff_max_minutes or 360),
        )
        backoff_minutes = min(max_backoff, base_backoff * (2 ** (failures - 1)))
        mp.auto_sync_next_run_at = now + timedelta(minutes=backoff_minutes)
        mp.updated_at = now
        db.add(mp)
        db.commit()

    def _run_once(self) -> None:
        db = SessionLocal()
        try:
            active_job = capture_job_service.get_active_job(db)
            if active_job:
                return

            now = utcnow()
            mp = self._pick_due_mp(db, now)
            if not mp:
                return

            try:
                wechat_client.ensure_login(db)
            except WeChatAuthError as exc:
                self._mark_dispatch_failure(db, mp, error=str(exc))
                return

            start_ts, end_ts = self._build_capture_range(mp, now)

            try:
                capture_job_service.create_job(
                    db,
                    mp=mp,
                    start_ts=start_ts,
                    end_ts=end_ts,
                    source="scheduled",
                )
            except ValueError:
                return
            except Exception as exc:  # noqa: BLE001
                self._mark_dispatch_failure(db, mp, error=str(exc))
                return

            mp.auto_sync_last_error = None
            mp.auto_sync_next_run_at = self._compute_next_run_at(
                base_time=now,
                interval_minutes=mp.auto_sync_interval_minutes,
            )
            mp.updated_at = now
            db.add(mp)
            db.commit()
        finally:
            db.close()

    def queue_due_now(
        self,
        db: Session,
        *,
        mp_id: str = "",
        favorite_only: bool = True,
        limit: int = 20,
    ) -> dict[str, Any]:
        query = db.query(MPAccount).filter(
            MPAccount.enabled.is_(True),
            MPAccount.auto_sync_enabled.is_(True),
        )

        cleaned_mp_id = mp_id.strip()
        if cleaned_mp_id:
            query = query.filter(MPAccount.id == cleaned_mp_id)
        elif favorite_only:
            query = query.filter(MPAccount.is_favorite.is_(True))

        rows = (
            query.order_by(
                MPAccount.auto_sync_next_run_at.asc(), MPAccount.updated_at.asc()
            )
            .limit(max(1, limit))
            .all()
        )

        now = utcnow()
        mp_ids: list[str] = []
        for row in rows:
            row.auto_sync_next_run_at = now
            row.auto_sync_last_error = None
            row.updated_at = now
            db.add(row)
            mp_ids.append(row.id)

        if rows:
            db.commit()

        return {
            "updated": len(rows),
            "mp_ids": mp_ids,
            "favorite_only": bool(favorite_only) and not bool(cleaned_mp_id),
        }

    def get_status(self, db: Session) -> dict[str, Any]:
        now = utcnow()
        enabled_count = (
            db.query(MPAccount)
            .filter(MPAccount.enabled.is_(True), MPAccount.auto_sync_enabled.is_(True))
            .count()
        )
        due_count = self._due_query(db, now).count()
        active_job = capture_job_service.get_active_job(db)
        auth = wechat_client.get_auth_state(db)

        return {
            "service_enabled": self.is_enabled(),
            "runner_alive": self.is_running(),
            "tick_seconds": max(10, int(settings.auto_sync_tick_seconds or 45)),
            "scheduled_mp_count": enabled_count,
            "due_count": due_count,
            "auth_status": auth.get("status", "unknown"),
            "active_job": {
                "id": active_job.id,
                "status": active_job.status,
                "mp_id": active_job.mp_id,
                "mp_nickname": active_job.mp_nickname,
                "source": active_job.source,
            }
            if active_job
            else None,
        }


auto_sync_service = AutoSyncService()
