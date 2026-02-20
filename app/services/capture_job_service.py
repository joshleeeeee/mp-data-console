import json
import random
import threading
import traceback
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import desc, or_
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.db import SessionLocal
from app.models import CaptureJob, CaptureJobLog, MPAccount
from app.services.article_service import article_service


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class CaptureJobService:
    ACTIVE_STATUSES = ("queued", "running", "canceling")
    RANGE_CAPTURE_PAGE_LIMIT = 300
    TERMINAL_STATUSES = ("success", "failed", "canceled")
    CANCEL_MESSAGE = "用户取消任务"
    JOB_SOURCES = ("manual", "scheduled", "retry")
    DEFAULT_SOURCE = "manual"

    def __init__(self) -> None:
        self._worker_lock = threading.Lock()
        self._active_ids_lock = threading.Lock()
        self._active_job_ids: set[str] = set()
        self._runtime_boot_at = utcnow()

    def _mark_job_active(self, job_id: str) -> None:
        with self._active_ids_lock:
            self._active_job_ids.add(job_id)

    def _mark_job_inactive(self, job_id: str) -> None:
        with self._active_ids_lock:
            self._active_job_ids.discard(job_id)

    def _snapshot_active_job_ids(self) -> set[str]:
        with self._active_ids_lock:
            return set(self._active_job_ids)

    @staticmethod
    def _as_unix_ts(value: datetime | None) -> float | None:
        if value is None:
            return None
        if value.tzinfo is None:
            return value.replace(tzinfo=timezone.utc).timestamp()
        return value.timestamp()

    def _reconcile_active_jobs(self, db: Session) -> None:
        runtime_active = self._snapshot_active_job_ids()
        changed = False

        legacy_cancelled_rows = (
            db.query(CaptureJob)
            .filter(
                CaptureJob.status == "canceled",
                CaptureJob.started_at.is_not(None),
                CaptureJob.finished_at.is_(None),
            )
            .all()
        )
        for row in legacy_cancelled_rows:
            if row.id in runtime_active:
                row.status = "canceling"
                row.error = row.error or "收到取消请求，等待当前步骤安全退出"
                self._append_log(
                    db,
                    row.id,
                    level="warn",
                    message="检测到历史取消中任务，状态已修正为 canceling",
                )
            else:
                row.error = row.error or self.CANCEL_MESSAGE
                row.finished_at = row.finished_at or utcnow()
            db.add(row)
            changed = True

        rows = (
            db.query(CaptureJob)
            .filter(CaptureJob.status.in_(self.ACTIVE_STATUSES))
            .all()
        )
        if not rows and not changed:
            return

        for row in rows:
            if row.id in runtime_active:
                continue

            reference_time = row.started_at or row.created_at
            reference_ts = self._as_unix_ts(reference_time)
            runtime_boot_ts = self._as_unix_ts(self._runtime_boot_at)
            interrupted_by_restart = bool(
                reference_ts is not None
                and runtime_boot_ts is not None
                and reference_ts < runtime_boot_ts
            )
            if interrupted_by_restart:
                reason = "任务在执行过程中遇到服务重启或热更新，线程已中断"
                hint = "若使用开发模式（uvicorn --reload），修改后端代码会中断进行中的后台抓取任务"
            else:
                reason = "任务执行线程异常中断"
                hint = "请查看服务端日志定位异常原因，建议重试该任务"

            row.status = "failed"
            if not row.error:
                row.error = f"{reason}，请重新发起抓取"
            if not row.finished_at:
                row.finished_at = utcnow()

            if row.source == "scheduled":
                self._update_scheduled_mp_state(
                    db,
                    mp_id=row.mp_id,
                    success=False,
                    error=row.error,
                )

            self._append_log(
                db,
                row.id,
                level="error",
                message="任务进程已中断，已自动标记失败",
                payload={
                    "reason": reason,
                    "hint": hint,
                    "runtime_boot_at": self._runtime_boot_at.isoformat(),
                    "job_created_at": row.created_at.isoformat()
                    if row.created_at
                    else None,
                    "job_started_at": row.started_at.isoformat()
                    if row.started_at
                    else None,
                },
            )
            db.add(row)
            changed = True

        if changed:
            db.commit()

    @staticmethod
    def _new_job_id() -> str:
        return f"job_{uuid.uuid4().hex[:18]}"

    @staticmethod
    def _result_dict(value: str | None) -> dict[str, Any]:
        if not value:
            return {}
        try:
            parsed = json.loads(value)
            if isinstance(parsed, dict):
                return parsed
        except json.JSONDecodeError:
            return {}
        return {}

    @staticmethod
    def _payload_dict(value: str | None) -> dict[str, Any] | None:
        if not value:
            return None
        try:
            parsed = json.loads(value)
            if isinstance(parsed, dict):
                return parsed
        except json.JSONDecodeError:
            return None
        return None

    def serialize_log(self, row: CaptureJobLog) -> dict[str, Any]:
        return {
            "id": row.id,
            "job_id": row.job_id,
            "level": row.level,
            "message": row.message,
            "payload": self._payload_dict(row.payload_json),
            "created_at": row.created_at.isoformat() if row.created_at else None,
        }

    def _append_log(
        self,
        db: Session,
        job_id: str,
        message: str,
        level: str = "info",
        payload: dict[str, Any] | None = None,
    ) -> CaptureJobLog:
        row = CaptureJobLog(
            job_id=job_id,
            level=level,
            message=message,
            payload_json=json.dumps(payload, ensure_ascii=False)
            if payload is not None
            else None,
        )
        db.add(row)
        return row

    def serialize_job(self, job: CaptureJob) -> dict[str, Any]:
        return {
            "id": job.id,
            "mp_id": job.mp_id,
            "mp_nickname": job.mp_nickname,
            "status": job.status,
            "source": job.source,
            "start_ts": job.start_ts,
            "end_ts": job.end_ts,
            "created": job.created_count,
            "updated": job.updated_count,
            "content_updated": job.content_updated_count,
            "duplicates_skipped": job.duplicates_skipped,
            "scanned_pages": job.scanned_pages,
            "max_pages": job.max_pages,
            "reached_target": job.reached_target,
            "error": job.error,
            "result": self._result_dict(job.result_json),
            "created_at": job.created_at.isoformat() if job.created_at else None,
            "started_at": job.started_at.isoformat() if job.started_at else None,
            "finished_at": job.finished_at.isoformat() if job.finished_at else None,
            "updated_at": job.updated_at.isoformat() if job.updated_at else None,
        }

    def list_jobs(
        self,
        db: Session,
        offset: int = 0,
        limit: int = 20,
        status: str = "",
        mp_id: str = "",
        source: str = "",
        keyword: str = "",
    ) -> tuple[list[dict[str, Any]], int]:
        self._reconcile_active_jobs(db)
        query = db.query(CaptureJob)

        if status.strip():
            query = query.filter(CaptureJob.status == status.strip())

        if mp_id.strip():
            query = query.filter(CaptureJob.mp_id == mp_id.strip())

        if source.strip():
            query = query.filter(CaptureJob.source == source.strip())

        if keyword.strip():
            term = f"%{keyword.strip()}%"
            query = query.filter(
                or_(
                    CaptureJob.mp_nickname.ilike(term),
                    CaptureJob.id.ilike(term),
                    CaptureJob.error.ilike(term),
                )
            )

        total = query.count()
        rows = (
            query.order_by(desc(CaptureJob.created_at))
            .offset(offset)
            .limit(limit)
            .all()
        )
        return [self.serialize_job(row) for row in rows], total

    def get_job(self, db: Session, job_id: str) -> dict[str, Any] | None:
        self._reconcile_active_jobs(db)
        row = db.query(CaptureJob).filter(CaptureJob.id == job_id).first()
        if not row:
            return None
        return self.serialize_job(row)

    def list_job_logs(
        self,
        db: Session,
        job_id: str,
        offset: int = 0,
        limit: int = 200,
    ) -> tuple[list[dict[str, Any]], int] | None:
        self._reconcile_active_jobs(db)
        job = self._get_job_row(db, job_id)
        if not job:
            return None

        query = db.query(CaptureJobLog).filter(CaptureJobLog.job_id == job_id)
        total = query.count()
        rows = (
            query.order_by(desc(CaptureJobLog.created_at), desc(CaptureJobLog.id))
            .offset(offset)
            .limit(limit)
            .all()
        )
        rows.reverse()
        return [self.serialize_log(row) for row in rows], total

    def _get_job_row(self, db: Session, job_id: str) -> CaptureJob | None:
        return db.query(CaptureJob).filter(CaptureJob.id == job_id).first()

    def get_active_job(self, db: Session) -> CaptureJob | None:
        self._reconcile_active_jobs(db)
        return (
            db.query(CaptureJob)
            .filter(CaptureJob.status.in_(self.ACTIVE_STATUSES))
            .order_by(desc(CaptureJob.created_at))
            .first()
        )

    def create_job(
        self,
        db: Session,
        mp: MPAccount,
        start_ts: int | None = None,
        end_ts: int | None = None,
        source: str = DEFAULT_SOURCE,
    ) -> dict[str, Any]:
        active_job = self.get_active_job(db)
        if active_job:
            raise ValueError(
                f"已有抓取任务在执行（{active_job.id}），请等待当前任务完成后再发起新任务"
            )

        if self._worker_lock.locked():
            raise ValueError("抓取任务执行器仍在收尾，请稍后再试")

        if start_ts is None or end_ts is None:
            raise ValueError("必须指定抓取时间范围")

        source_name = (source or self.DEFAULT_SOURCE).strip().lower()
        if source_name not in self.JOB_SOURCES:
            raise ValueError(f"不支持的任务来源：{source}")

        start = int(start_ts)
        end = int(end_ts)
        if start > end:
            start, end = end, start

        job = CaptureJob(
            id=self._new_job_id(),
            mp_id=mp.id,
            mp_nickname=mp.nickname,
            status="queued",
            source=source_name,
            pages_hint=self.RANGE_CAPTURE_PAGE_LIMIT,
            requested_count=0,
            start_ts=start,
            end_ts=end,
            fetch_content=True,
        )
        db.add(job)
        self._append_log(
            db,
            job.id,
            message="任务已创建，等待执行",
            payload={
                "source": source_name,
                "start_ts": start,
                "end_ts": end,
                "max_pages": self.RANGE_CAPTURE_PAGE_LIMIT,
            },
        )
        db.commit()
        db.refresh(job)
        try:
            article_service.mark_mp_used(db, mp)
        except Exception:  # noqa: BLE001
            db.rollback()

        self._mark_job_active(job.id)
        thread = threading.Thread(
            target=self._run_job,
            args=(job.id,),
            daemon=True,
            name=f"capture-job-{job.id[:8]}",
        )
        thread.start()

        return self.serialize_job(job)

    def cancel_job(self, db: Session, job_id: str) -> dict[str, Any] | None:
        self._reconcile_active_jobs(db)
        job = self._get_job_row(db, job_id)
        if not job:
            return None

        if job.status in self.TERMINAL_STATUSES:
            raise ValueError("任务已结束，无法取消")

        if job.status == "queued":
            job.status = "canceled"
            job.error = self.CANCEL_MESSAGE
            job.finished_at = utcnow()
            self._append_log(
                db,
                job.id,
                level="warn",
                message="任务在排队阶段被取消",
            )
            self._mark_job_inactive(job.id)
        elif job.status == "running":
            job.status = "canceling"
            job.error = "收到取消请求，等待当前步骤安全退出"
            self._append_log(
                db,
                job.id,
                level="warn",
                message="收到取消请求，正在停止任务",
            )
        else:
            job.error = job.error or "收到取消请求，等待当前步骤安全退出"

        db.add(job)
        db.commit()
        db.refresh(job)
        return self.serialize_job(job)

    def retry_job(self, db: Session, job_id: str) -> dict[str, Any] | None:
        self._reconcile_active_jobs(db)
        source = self._get_job_row(db, job_id)
        if not source:
            return None

        if source.status in self.ACTIVE_STATUSES:
            raise ValueError("任务仍在执行中，无法重试")

        if source.start_ts is None or source.end_ts is None:
            raise ValueError("该任务缺少时间范围，无法重试")

        mp = db.query(MPAccount).filter(MPAccount.id == source.mp_id).first()
        if not mp:
            raise ValueError("任务对应公众号不存在，无法重试")

        self._append_log(
            db,
            source.id,
            message="已发起重试任务",
            payload={"mp_id": source.mp_id},
        )
        db.commit()

        return self.create_job(
            db,
            mp=mp,
            start_ts=int(source.start_ts),
            end_ts=int(source.end_ts),
            source="retry",
        )

    def _compute_auto_sync_next_run(
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

    def _update_scheduled_mp_state(
        self,
        db: Session,
        *,
        mp_id: str,
        success: bool,
        error: str | None = None,
    ) -> None:
        mp = db.query(MPAccount).filter(MPAccount.id == mp_id).first()
        if not mp or not bool(mp.auto_sync_enabled):
            return

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

        if success:
            mp.auto_sync_last_success_at = now
            mp.auto_sync_last_error = None
            mp.auto_sync_consecutive_failures = 0
            mp.auto_sync_next_run_at = self._compute_auto_sync_next_run(
                base_time=now,
                interval_minutes=mp.auto_sync_interval_minutes,
            )
        else:
            failures = max(0, int(mp.auto_sync_consecutive_failures or 0)) + 1
            mp.auto_sync_consecutive_failures = failures
            message = (error or "").strip() or "自动同步任务失败"
            mp.auto_sync_last_error = message[:1000]

            base_backoff = max(
                1,
                int(settings.auto_sync_failure_backoff_base_minutes or 15),
            )
            max_backoff = max(
                base_backoff,
                int(settings.auto_sync_failure_backoff_max_minutes or 360),
            )
            backoff_minutes = min(max_backoff, base_backoff * (2 ** (failures - 1)))
            mp.auto_sync_next_run_at = now + timedelta(minutes=backoff_minutes)

        mp.updated_at = now
        db.add(mp)

    def _run_job(self, job_id: str) -> None:
        with self._worker_lock:
            db = SessionLocal()
            try:
                job = self._get_job_row(db, job_id)
                if not job:
                    return

                if job.status == "canceled":
                    if not job.error:
                        job.error = self.CANCEL_MESSAGE
                    if not job.finished_at:
                        job.finished_at = utcnow()
                    self._append_log(
                        db,
                        job.id,
                        level="warn",
                        message="任务已取消，未进入执行阶段",
                    )
                    if job.source == "scheduled":
                        self._update_scheduled_mp_state(
                            db,
                            mp_id=job.mp_id,
                            success=False,
                            error=job.error,
                        )
                    db.add(job)
                    db.commit()
                    return

                if job.status != "queued":
                    return

                job.status = "running"
                job.started_at = utcnow()
                job.error = None
                self._append_log(
                    db,
                    job.id,
                    message="任务开始执行",
                    payload={
                        "start_ts": job.start_ts,
                        "end_ts": job.end_ts,
                        "max_pages": job.pages_hint,
                    },
                )
                db.add(job)
                db.commit()
                db.refresh(job)

                mp = db.query(MPAccount).filter(MPAccount.id == job.mp_id).first()
                if not mp:
                    raise RuntimeError("抓取目标公众号不存在")

                last_logged_progress = {"page": 0}

                def should_stop() -> bool:
                    live_job = self._get_job_row(db, job_id)
                    return bool(
                        live_job and live_job.status in ("canceling", "canceled")
                    )

                def on_progress(progress: dict[str, Any]) -> None:
                    live_job = self._get_job_row(db, job_id)
                    if not live_job:
                        return

                    live_job.created_count = int(progress.get("created", 0) or 0)
                    live_job.updated_count = int(progress.get("updated", 0) or 0)
                    live_job.content_updated_count = int(
                        progress.get("content_updated", 0) or 0
                    )
                    live_job.duplicates_skipped = int(
                        progress.get("duplicates_skipped", 0) or 0
                    )
                    live_job.scanned_pages = int(progress.get("scanned_pages", 0) or 0)
                    live_job.max_pages = int(progress.get("max_pages", 0) or 0)
                    live_job.reached_target = bool(
                        progress.get("reached_target", False)
                    )

                    current_page = int(progress.get("scanned_pages", 0) or 0)
                    if current_page > last_logged_progress["page"]:
                        last_logged_progress["page"] = current_page
                        self._append_log(
                            db,
                            live_job.id,
                            message=f"扫描进度更新：第 {current_page} 页",
                            payload={
                                "created": live_job.created_count,
                                "updated": live_job.updated_count,
                                "duplicates_skipped": live_job.duplicates_skipped,
                                "max_pages": live_job.max_pages,
                            },
                        )

                    db.add(live_job)
                    db.commit()

                result = article_service.sync_mp_articles(
                    db,
                    mp=mp,
                    pages=job.pages_hint,
                    fetch_content=True,
                    start_ts=job.start_ts,
                    end_ts=job.end_ts,
                    progress_callback=on_progress,
                    should_stop=should_stop,
                )

                done_job = self._get_job_row(db, job_id)
                if not done_job:
                    return

                done_job.created_count = int(result.get("created", 0) or 0)
                done_job.updated_count = int(result.get("updated", 0) or 0)
                done_job.content_updated_count = int(
                    result.get("content_updated", 0) or 0
                )
                done_job.duplicates_skipped = int(
                    result.get("duplicates_skipped", 0) or 0
                )
                done_job.scanned_pages = int(result.get("scanned_pages", 0) or 0)
                done_job.max_pages = int(result.get("max_pages", 0) or 0)
                done_job.reached_target = bool(result.get("reached_target", False))
                done_job.result_json = json.dumps(result, ensure_ascii=False)
                done_job.finished_at = utcnow()

                if bool(result.get("cancelled")) or done_job.status in (
                    "canceling",
                    "canceled",
                ):
                    done_job.status = "canceled"
                    done_job.error = done_job.error or self.CANCEL_MESSAGE
                    if done_job.source == "scheduled":
                        self._update_scheduled_mp_state(
                            db,
                            mp_id=done_job.mp_id,
                            success=False,
                            error=done_job.error,
                        )
                    self._append_log(
                        db,
                        done_job.id,
                        level="warn",
                        message="任务已取消",
                        payload={
                            "created": done_job.created_count,
                            "updated": done_job.updated_count,
                            "duplicates_skipped": done_job.duplicates_skipped,
                            "scanned_pages": done_job.scanned_pages,
                        },
                    )
                else:
                    done_job.status = "success"
                    done_job.error = None
                    if done_job.source == "scheduled":
                        self._update_scheduled_mp_state(
                            db,
                            mp_id=done_job.mp_id,
                            success=True,
                        )
                    self._append_log(
                        db,
                        done_job.id,
                        message="任务执行完成",
                        payload={
                            "created": done_job.created_count,
                            "updated": done_job.updated_count,
                            "duplicates_skipped": done_job.duplicates_skipped,
                            "scanned_pages": done_job.scanned_pages,
                        },
                    )

                db.add(done_job)
                db.commit()
            except Exception as exc:  # noqa: BLE001
                db.rollback()
                failed_job = self._get_job_row(db, job_id)
                if failed_job:
                    error_type = exc.__class__.__name__
                    error_text = str(exc).strip() or repr(exc)
                    traceback_text = traceback.format_exc()
                    if len(traceback_text) > 12000:
                        traceback_text = (
                            f"{traceback_text[:12000]}\n... <traceback truncated>"
                        )

                    if failed_job.status in ("canceling", "canceled"):
                        failed_job.status = "canceled"
                        failed_job.error = failed_job.error or self.CANCEL_MESSAGE
                    else:
                        failed_job.status = "failed"
                        failed_job.error = f"{error_type}: {error_text}"

                    if failed_job.source == "scheduled":
                        self._update_scheduled_mp_state(
                            db,
                            mp_id=failed_job.mp_id,
                            success=False,
                            error=failed_job.error,
                        )

                    failed_job.finished_at = utcnow()
                    self._append_log(
                        db,
                        failed_job.id,
                        level="error" if failed_job.status == "failed" else "warn",
                        message="任务执行失败"
                        if failed_job.status == "failed"
                        else "任务已取消",
                        payload={
                            "error_type": error_type,
                            "error": error_text,
                            "traceback": traceback_text,
                        },
                    )
                    db.add(failed_job)
                    db.commit()
            finally:
                self._mark_job_inactive(job_id)
                db.close()


capture_job_service = CaptureJobService()
