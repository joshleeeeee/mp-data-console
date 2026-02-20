import json
import threading
import uuid
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import desc, or_
from sqlalchemy.orm import Session

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

    def __init__(self) -> None:
        self._worker_lock = threading.Lock()
        self._active_ids_lock = threading.Lock()
        self._active_job_ids: set[str] = set()

    def _mark_job_active(self, job_id: str) -> None:
        with self._active_ids_lock:
            self._active_job_ids.add(job_id)

    def _mark_job_inactive(self, job_id: str) -> None:
        with self._active_ids_lock:
            self._active_job_ids.discard(job_id)

    def _snapshot_active_job_ids(self) -> set[str]:
        with self._active_ids_lock:
            return set(self._active_job_ids)

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
            row.status = "failed"
            if not row.error:
                row.error = "任务进程已中断，请重新发起抓取"
            if not row.finished_at:
                row.finished_at = utcnow()
            self._append_log(
                db,
                row.id,
                level="error",
                message="任务进程已中断，已自动标记失败",
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
        keyword: str = "",
    ) -> tuple[list[dict[str, Any]], int]:
        self._reconcile_active_jobs(db)
        query = db.query(CaptureJob)

        if status.strip():
            query = query.filter(CaptureJob.status == status.strip())

        if mp_id.strip():
            query = query.filter(CaptureJob.mp_id == mp_id.strip())

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

        start = int(start_ts)
        end = int(end_ts)
        if start > end:
            start, end = end, start

        job = CaptureJob(
            id=self._new_job_id(),
            mp_id=mp.id,
            mp_nickname=mp.nickname,
            status="queued",
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
        )

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
                    if failed_job.status in ("canceling", "canceled"):
                        failed_job.status = "canceled"
                        failed_job.error = failed_job.error or self.CANCEL_MESSAGE
                    else:
                        failed_job.status = "failed"
                        failed_job.error = str(exc)
                    failed_job.finished_at = utcnow()
                    self._append_log(
                        db,
                        failed_job.id,
                        level="error" if failed_job.status == "failed" else "warn",
                        message="任务执行失败"
                        if failed_job.status == "failed"
                        else "任务已取消",
                        payload={"error": str(exc)},
                    )
                    db.add(failed_job)
                    db.commit()
            finally:
                self._mark_job_inactive(job_id)
                db.close()


capture_job_service = CaptureJobService()
