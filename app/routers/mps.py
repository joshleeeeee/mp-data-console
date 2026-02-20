from datetime import date, datetime, time, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from app.core.db import get_db
from app.schemas import (
    ApiResponse,
    MPCreateRequest,
    MPFavoriteUpdateRequest,
    MPSyncRequest,
    MPOut,
)
from app.services.article_service import article_service
from app.services.capture_job_service import capture_job_service
from app.services.wechat_client import WeChatAuthError, wechat_client

router = APIRouter(prefix="/mps", tags=["mps"])

CHINA_TZ = timezone(timedelta(hours=8))


def _date_start_to_ts(value: date | None) -> int | None:
    if value is None:
        return None
    return int(datetime.combine(value, time.min, tzinfo=CHINA_TZ).timestamp())


def _date_end_to_ts(value: date | None) -> int | None:
    if value is None:
        return None
    return int(datetime.combine(value, time.max, tzinfo=CHINA_TZ).timestamp())


@router.get("/search", response_model=ApiResponse)
def search_mps(
    keyword: str = Query("", description="公众号关键词"),
    offset: int = Query(0, ge=0),
    limit: int = Query(10, ge=1, le=50),
    db: Session = Depends(get_db),
):
    try:
        data = wechat_client.search_mps(db, keyword=keyword, offset=offset, limit=limit)
        return ApiResponse(data=data)
    except WeChatAuthError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=500, detail=f"搜索公众号失败: {exc}") from exc


@router.post("", response_model=ApiResponse)
def add_mp(payload: MPCreateRequest, db: Session = Depends(get_db)):
    mp = article_service.create_or_update_mp(
        db,
        fakeid=payload.fakeid,
        nickname=payload.nickname,
        alias=payload.alias,
        avatar=payload.avatar,
        intro=payload.intro,
        biz=payload.biz,
    )
    return ApiResponse(data=MPOut.model_validate(mp).model_dump())


@router.get("", response_model=ApiResponse)
def list_mps(
    offset: int = Query(0, ge=0),
    limit: int = Query(20, ge=1, le=100),
    favorite_only: bool = Query(False, description="仅返回常用公众号"),
    db: Session = Depends(get_db),
):
    rows, total = article_service.list_mps(
        db,
        offset=offset,
        limit=limit,
        favorite_only=favorite_only,
    )
    return ApiResponse(
        data={
            "total": total,
            "offset": offset,
            "limit": limit,
            "favorite_only": favorite_only,
            "list": [MPOut.model_validate(item).model_dump() for item in rows],
        }
    )


@router.patch("/{mp_id}/favorite", response_model=ApiResponse)
def set_mp_favorite(
    mp_id: str,
    payload: MPFavoriteUpdateRequest,
    db: Session = Depends(get_db),
):
    mp = article_service.set_mp_favorite(db, mp_id, payload.is_favorite)
    if not mp:
        raise HTTPException(status_code=404, detail="公众号不存在")
    return ApiResponse(data=MPOut.model_validate(mp).model_dump())


@router.post("/{mp_id}/sync/jobs", response_model=ApiResponse)
def create_sync_job(mp_id: str, payload: MPSyncRequest, db: Session = Depends(get_db)):
    mp = article_service.get_mp(db, mp_id)
    if not mp:
        raise HTTPException(status_code=404, detail="公众号不存在")

    start_ts = _date_start_to_ts(payload.date_start)
    end_ts = _date_end_to_ts(payload.date_end)

    try:
        wechat_client.ensure_login(db)
        job = capture_job_service.create_job(
            db,
            mp=mp,
            start_ts=start_ts,
            end_ts=end_ts,
        )
        return ApiResponse(data=job)
    except WeChatAuthError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=500, detail=f"创建抓取任务失败: {exc}") from exc


@router.get("/sync/jobs", response_model=ApiResponse)
def list_sync_jobs(
    offset: int = Query(0, ge=0),
    limit: int = Query(20, ge=1, le=100),
    status: str = Query("", description="任务状态过滤"),
    mp_id: str = Query("", description="按公众号 ID 过滤"),
    keyword: str = Query("", description="按任务 ID/公众号名/错误关键词过滤"),
    db: Session = Depends(get_db),
):
    rows, total = capture_job_service.list_jobs(
        db,
        offset=offset,
        limit=limit,
        status=status,
        mp_id=mp_id,
        keyword=keyword,
    )
    return ApiResponse(
        data={
            "total": total,
            "offset": offset,
            "limit": limit,
            "status": status,
            "mp_id": mp_id,
            "keyword": keyword,
            "list": rows,
        }
    )


@router.get("/sync/jobs/{job_id}", response_model=ApiResponse)
def get_sync_job(job_id: str, db: Session = Depends(get_db)):
    job = capture_job_service.get_job(db, job_id)
    if not job:
        raise HTTPException(status_code=404, detail="抓取任务不存在")
    return ApiResponse(data=job)


@router.get("/sync/jobs/{job_id}/logs", response_model=ApiResponse)
def list_sync_job_logs(
    job_id: str,
    offset: int = Query(0, ge=0),
    limit: int = Query(200, ge=1, le=500),
    db: Session = Depends(get_db),
):
    result = capture_job_service.list_job_logs(
        db,
        job_id=job_id,
        offset=offset,
        limit=limit,
    )
    if result is None:
        raise HTTPException(status_code=404, detail="抓取任务不存在")
    rows, total = result
    return ApiResponse(
        data={
            "total": total,
            "offset": offset,
            "limit": limit,
            "list": rows,
        }
    )


@router.post("/sync/jobs/{job_id}/cancel", response_model=ApiResponse)
def cancel_sync_job(job_id: str, db: Session = Depends(get_db)):
    try:
        job = capture_job_service.cancel_job(db, job_id)
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc

    if not job:
        raise HTTPException(status_code=404, detail="抓取任务不存在")
    return ApiResponse(data=job)


@router.post("/sync/jobs/{job_id}/retry", response_model=ApiResponse)
def retry_sync_job(job_id: str, db: Session = Depends(get_db)):
    try:
        wechat_client.ensure_login(db)
        job = capture_job_service.retry_job(db, job_id)
    except WeChatAuthError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=500, detail=f"重试任务失败: {exc}") from exc

    if not job:
        raise HTTPException(status_code=404, detail="抓取任务不存在")
    return ApiResponse(data=job)


@router.post("/{mp_id}/sync", response_model=ApiResponse)
def sync_mp(mp_id: str, payload: MPSyncRequest, db: Session = Depends(get_db)):
    mp = article_service.get_mp(db, mp_id)
    if not mp:
        raise HTTPException(status_code=404, detail="公众号不存在")

    start_ts = _date_start_to_ts(payload.date_start)
    end_ts = _date_end_to_ts(payload.date_end)

    try:
        result = article_service.sync_mp_articles(
            db,
            mp=mp,
            pages=capture_job_service.RANGE_CAPTURE_PAGE_LIMIT,
            fetch_content=True,
            start_ts=start_ts,
            end_ts=end_ts,
        )
        return ApiResponse(data=result)
    except WeChatAuthError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=500, detail=f"同步公众号失败: {exc}") from exc
