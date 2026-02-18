from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import FileResponse
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.db import get_db
from app.schemas import ApiResponse
from app.services.wechat_client import WeChatAuthError, wechat_client

router = APIRouter(prefix="/auth", tags=["auth"])


@router.get("/qr", response_model=ApiResponse)
def get_login_qr(db: Session = Depends(get_db)):
    try:
        data = wechat_client.request_qr_code(db)
        return ApiResponse(data=data)
    except WeChatAuthError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=500, detail=f"获取二维码失败: {exc}") from exc


@router.get("/qr/image")
def get_login_qr_image():
    file_path = Path(settings.qr_file)
    if not file_path.exists():
        raise HTTPException(status_code=404, detail="二维码不存在，请先请求 /auth/qr")
    return FileResponse(file_path)


@router.get("/status", response_model=ApiResponse)
def get_login_status(db: Session = Depends(get_db)):
    try:
        data = wechat_client.poll_login_status(db)
        return ApiResponse(data=data)
    except WeChatAuthError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("/session", response_model=ApiResponse)
def get_session_info(db: Session = Depends(get_db)):
    data = wechat_client.get_auth_state(db)
    return ApiResponse(data=data)


@router.post("/logout", response_model=ApiResponse)
def logout(db: Session = Depends(get_db)):
    wechat_client.logout(db)
    return ApiResponse(message="已注销")
