import json
import threading
import uuid
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import desc
from sqlalchemy.orm import Session

from app.core.db import SessionLocal
from app.models import CaptureJob, MPAccount
from app.services.article_service import article_service


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class CaptureJobService:
    ACTIVE_STATUSES = ("queued", "running")

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
        rows = (
            db.query(CaptureJob)
            .filter(CaptureJob.status.in_(self.ACTIVE_STATUSES))
            .all()
        )
        if not rows:
            return

        runtime_active = self._snapshot_active_job_ids()
        changed = False
        for row in rows:
            if row.id in runtime_active:
                continue
            row.status = "failed"
            if not row.error:
                row.error = "任务进程已中断，请重新发起抓取"
            if not row.finished_at:
                row.finished_at = utcnow()
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

    def serialize_job(self, job: CaptureJob) -> dict[str, Any]:
        return {
            "id": job.id,
            "mp_id": job.mp_id,
            "mp_nickname": job.mp_nickname,
            "status": job.status,
            "pages_hint": job.pages_hint,
            "requested_count": job.requested_count,
            "start_ts": job.start_ts,
            "end_ts": job.end_ts,
            "fetch_content": job.fetch_content,
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
    ) -> tuple[list[dict[str, Any]], int]:
        self._reconcile_active_jobs(db)
        query = db.query(CaptureJob)
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
        pages: int,
        fetch_content: bool,
        target_count: int | None = None,
        start_ts: int | None = None,
        end_ts: int | None = None,
    ) -> dict[str, Any]:
        active_job = self.get_active_job(db)
        if active_job:
            raise ValueError(
                f"已有抓取任务在执行（{active_job.id}），请等待当前任务完成后再发起新任务"
            )

        pages_hint = max(1, int(pages))
        has_date_range = start_ts is not None or end_ts is not None
        requested_count = (
            0
            if has_date_range
            else (
                max(1, int(target_count))
                if target_count is not None
                else pages_hint * 5
            )
        )

        job = CaptureJob(
            id=self._new_job_id(),
            mp_id=mp.id,
            mp_nickname=mp.nickname,
            status="queued",
            pages_hint=pages_hint,
            requested_count=requested_count,
            start_ts=start_ts,
            end_ts=end_ts,
            fetch_content=bool(fetch_content),
        )
        db.add(job)
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

    def _run_job(self, job_id: str) -> None:
        with self._worker_lock:
            db = SessionLocal()
            try:
                job = self._get_job_row(db, job_id)
                if not job:
                    return

                job.status = "running"
                job.started_at = utcnow()
                job.error = None
                db.add(job)
                db.commit()
                db.refresh(job)

                mp = db.query(MPAccount).filter(MPAccount.id == job.mp_id).first()
                if not mp:
                    raise RuntimeError("抓取目标公众号不存在")

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
                    db.add(live_job)
                    db.commit()

                result = article_service.sync_mp_articles(
                    db,
                    mp=mp,
                    pages=job.pages_hint,
                    fetch_content=job.fetch_content,
                    target_count=job.requested_count,
                    start_ts=job.start_ts,
                    end_ts=job.end_ts,
                    progress_callback=on_progress,
                )

                done_job = self._get_job_row(db, job_id)
                if not done_job:
                    return

                done_job.status = "success"
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
                done_job.error = None
                done_job.result_json = json.dumps(result, ensure_ascii=False)
                done_job.finished_at = utcnow()
                db.add(done_job)
                db.commit()
            except Exception as exc:  # noqa: BLE001
                db.rollback()
                failed_job = self._get_job_row(db, job_id)
                if failed_job:
                    failed_job.status = "failed"
                    failed_job.error = str(exc)
                    failed_job.finished_at = utcnow()
                    db.add(failed_job)
                    db.commit()
            finally:
                self._mark_job_inactive(job_id)
                db.close()


capture_job_service = CaptureJobService()
