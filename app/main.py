from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.core.config import settings
from app.core.db import init_db
from app.routers import articles, assets, auth, exports, mps, ops

app = FastAPI(
    title=settings.app_name,
    version="0.1.0",
    description="Mini 微信公众号抓取工具（扫码登录 + 文章抓取 + 导出/PDF）",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.on_event("startup")
def on_startup() -> None:
    init_db()


@app.get("/")
def index():
    return {
        "name": settings.app_name,
        "version": "0.1.0",
        "docs": "/docs",
        "api_prefix": settings.api_prefix,
    }


app.include_router(auth.router, prefix=settings.api_prefix)
app.include_router(mps.router, prefix=settings.api_prefix)
app.include_router(articles.router, prefix=settings.api_prefix)
app.include_router(exports.router, prefix=settings.api_prefix)
app.include_router(assets.router, prefix=settings.api_prefix)
app.include_router(ops.router, prefix=settings.api_prefix)
