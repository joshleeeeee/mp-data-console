from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import FileResponse
from sqlalchemy.orm import Session

from app.core.db import get_db
from app.schemas import ApiResponse, BatchExportRequest, ExportRequest
from app.services.article_service import article_service
from app.services.export_service import ExportError, export_service

router = APIRouter(prefix="/exports", tags=["exports"])


@router.post("/article/{article_id}", response_model=ApiResponse)
def export_single_article(
    article_id: str,
    payload: ExportRequest,
    db: Session = Depends(get_db),
):
    article = article_service.get_article(db, article_id)
    if not article:
        raise HTTPException(status_code=404, detail="文章不存在")

    try:
        result = export_service.export_article(article, payload.format)
        return ApiResponse(data=result)
    except ExportError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/batch", response_model=ApiResponse)
def export_batch(payload: BatchExportRequest, db: Session = Depends(get_db)):
    try:
        result = export_service.export_batch(
            db,
            article_ids=payload.article_ids,
            export_format=payload.format,
        )
        return ApiResponse(data=result)
    except ExportError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("/files/{relative_path:path}")
def download_export(relative_path: str):
    try:
        file_path = export_service.resolve_file(relative_path)
        return FileResponse(file_path)
    except ExportError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
