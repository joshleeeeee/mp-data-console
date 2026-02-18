from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from app.core.db import get_db
from app.schemas import ApiResponse, ArticleOut
from app.services.article_service import article_service
from app.services.wechat_client import WeChatAuthError

router = APIRouter(prefix="/articles", tags=["articles"])


@router.get("", response_model=ApiResponse)
def list_articles(
    mp_id: str | None = Query(default=None),
    keyword: str | None = Query(default=None),
    offset: int = Query(0, ge=0),
    limit: int = Query(20, ge=1, le=100),
    db: Session = Depends(get_db),
):
    rows, total = article_service.list_articles(
        db,
        mp_id=mp_id,
        keyword=keyword,
        offset=offset,
        limit=limit,
    )
    return ApiResponse(
        data={
            "total": total,
            "offset": offset,
            "limit": limit,
            "list": [ArticleOut.model_validate(item).model_dump() for item in rows],
        }
    )


@router.get("/{article_id}", response_model=ApiResponse)
def get_article(article_id: str, db: Session = Depends(get_db)):
    article = article_service.get_article(db, article_id)
    if not article:
        raise HTTPException(status_code=404, detail="文章不存在")
    return ApiResponse(data=ArticleOut.model_validate(article).model_dump())


@router.post("/{article_id}/refresh", response_model=ApiResponse)
def refresh_article(article_id: str, db: Session = Depends(get_db)):
    article = article_service.get_article(db, article_id)
    if not article:
        raise HTTPException(status_code=404, detail="文章不存在")

    try:
        refreshed = article_service.refresh_article_content(db, article)
        return ApiResponse(data=ArticleOut.model_validate(refreshed).model_dump())
    except WeChatAuthError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=500, detail=f"刷新文章失败: {exc}") from exc
