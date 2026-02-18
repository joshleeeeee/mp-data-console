from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from app.core.db import get_db
from app.schemas import ApiResponse, MPCreateRequest, MPSyncRequest, MPOut
from app.services.article_service import article_service
from app.services.capture_job_service import capture_job_service
from app.services.wechat_client import WeChatAuthError, wechat_client

router = APIRouter(prefix="/mps", tags=["mps"])


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
    db: Session = Depends(get_db),
):
    rows, total = article_service.list_mps(db, offset=offset, limit=limit)
    return ApiResponse(
        data={
            "total": total,
            "offset": offset,
            "limit": limit,
            "list": [MPOut.model_validate(item).model_dump() for item in rows],
        }
    )


@router.post("/{mp_id}/sync/jobs", response_model=ApiResponse)
def create_sync_job(mp_id: str, payload: MPSyncRequest, db: Session = Depends(get_db)):
    mp = article_service.get_mp(db, mp_id)
    if not mp:
        raise HTTPException(status_code=404, detail="公众号不存在")

    try:
        wechat_client.ensure_login(db)
        job = capture_job_service.create_job(
            db,
            mp=mp,
            pages=payload.pages,
            fetch_content=payload.fetch_content,
            target_count=payload.target_count,
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
    db: Session = Depends(get_db),
):
    rows, total = capture_job_service.list_jobs(db, offset=offset, limit=limit)
    return ApiResponse(
        data={
            "total": total,
            "offset": offset,
            "limit": limit,
            "list": rows,
        }
    )


@router.get("/sync/jobs/{job_id}", response_model=ApiResponse)
def get_sync_job(job_id: str, db: Session = Depends(get_db)):
    job = capture_job_service.get_job(db, job_id)
    if not job:
        raise HTTPException(status_code=404, detail="抓取任务不存在")
    return ApiResponse(data=job)


@router.post("/{mp_id}/sync", response_model=ApiResponse)
def sync_mp(mp_id: str, payload: MPSyncRequest, db: Session = Depends(get_db)):
    mp = article_service.get_mp(db, mp_id)
    if not mp:
        raise HTTPException(status_code=404, detail="公众号不存在")

    try:
        result = article_service.sync_mp_articles(
            db,
            mp=mp,
            pages=payload.pages,
            fetch_content=payload.fetch_content,
            target_count=payload.target_count,
        )
        return ApiResponse(data=result)
    except WeChatAuthError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=500, detail=f"同步公众号失败: {exc}") from exc
