from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import Response

from app.services.image_service import ImageProxyError, image_proxy_service

router = APIRouter(prefix="/assets", tags=["assets"])


@router.get("/image")
def proxy_wechat_image(
    url: str = Query(..., description="原始图片URL"),
    force: bool = Query(False, description="是否强制跳过缓存"),
):
    try:
        content, content_type, from_cache = image_proxy_service.fetch_image(
            url, force=force
        )
        headers = {
            "Cache-Control": "public, max-age=86400",
            "X-Image-Cache": "HIT" if from_cache else "MISS",
        }
        return Response(content=content, media_type=content_type, headers=headers)
    except ImageProxyError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=500, detail=f"图片代理异常: {exc}") from exc
