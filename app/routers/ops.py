import json
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import MetaData, String, Table, and_, cast, func, inspect, or_, select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.db import get_db
from app.models import Article, MPAccount
from app.schemas import ApiResponse, QuickSyncRequest
from app.services.article_service import article_service
from app.services.wechat_client import WeChatAuthError, wechat_client

router = APIRouter(prefix="/ops", tags=["ops"])

MCP_SERVER_NAME = "we-mp-mini-articles"
MCP_SERVER_MODULE = "app.mcp_server"
MCP_TOOL_DOCS = [
    {
        "name": "list_mps",
        "description": "列出库内公众号及文章数量，支持关键词筛选",
    },
    {
        "name": "list_articles_by_mp",
        "description": "按公众号名或 mp_id 列出对应文章列表",
    },
    {
        "name": "db_overview",
        "description": "查看数据库中的公众号/文章数量和最新抓取文章",
    },
    {
        "name": "search_articles",
        "description": "按关键词检索文章标题/正文，并返回正文预览",
    },
    {
        "name": "get_article_text",
        "description": "按 article_id 或 url 读取文章全文文本",
    },
]


TABLE_COMMENTS: dict[str, str] = {
    "auth_sessions": "登录会话信息（扫码状态、token、cookie）",
    "mps": "已保存的公众号清单",
    "articles": "抓取到的文章正文与元数据",
    "capture_jobs": "后台抓取任务状态与结果",
}


COLUMN_COMMENTS: dict[str, dict[str, str]] = {
    "auth_sessions": {
        "status": "登录状态",
        "token": "微信后台 token",
        "cookie_json": "登录 cookie(JSON)",
        "account_name": "当前登录账号",
        "last_error": "最近一次错误",
        "updated_at": "更新时间",
    },
    "mps": {
        "id": "内部公众号 ID",
        "fakeid": "微信 fakeid",
        "nickname": "公众号名称",
        "alias": "公众号微信号",
        "avatar": "头像链接",
        "last_sync_at": "最近同步时间",
    },
    "articles": {
        "id": "内部文章 ID",
        "mp_id": "归属公众号 ID",
        "title": "文章标题",
        "url": "原文链接",
        "author": "作者",
        "publish_ts": "发布时间戳",
        "content_html": "正文 HTML",
        "content_text": "正文纯文本",
        "images_json": "正文图片列表(JSON)",
        "updated_at": "更新时间",
    },
    "capture_jobs": {
        "id": "抓取任务 ID",
        "mp_id": "公众号 ID",
        "mp_nickname": "公众号名称快照",
        "status": "任务状态",
        "pages_hint": "提交时页数参数",
        "requested_count": "目标去重条数",
        "fetch_content": "是否抓正文",
        "created_count": "新增文章数",
        "updated_count": "更新文章数",
        "content_updated_count": "正文更新数",
        "duplicates_skipped": "重复跳过数",
        "scanned_pages": "已扫描页数",
        "max_pages": "扫描页数上限",
        "reached_target": "是否达成目标",
        "error": "任务错误信息",
        "created_at": "创建时间",
        "started_at": "开始时间",
        "finished_at": "结束时间",
        "updated_at": "更新时间",
    },
}


def _serialize_value(value: Any) -> Any:
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, (bytes, bytearray)):
        return f"<bytes:{len(value)}>"
    return value


def _resolve_sqlite_path() -> str:
    db_url = settings.database_url
    if not db_url.startswith("sqlite:///"):
        return db_url
    raw_path = db_url.removeprefix("sqlite:///")
    path = Path(raw_path)
    if not path.is_absolute():
        path = (Path.cwd() / path).resolve()
    return str(path)


def _resolve_python_command() -> str:
    executable = sys.executable.strip() if sys.executable else ""
    return executable or "python3"


def _build_mcp_install_steps(python_command: str, database_path: str) -> list[str]:
    return [
        "在项目根目录创建并激活虚拟环境（可选但推荐）",
        "安装依赖：pip install -r requirements.txt",
        (
            "本地验证 MCP 服务可启动："
            f'{python_command} -m {MCP_SERVER_MODULE} --db-path "{database_path}"'
        ),
        "将下方 MCP 配置粘贴到你的 MCP 客户端（如 Claude Desktop / Cursor）",
    ]


def _parse_search_columns(raw_columns: str, all_columns: list[str]) -> list[str]:
    if not raw_columns:
        return []

    allow = set(all_columns)
    selected = []
    for item in raw_columns.split(","):
        name = item.strip()
        if not name:
            continue
        if name in allow:
            selected.append(name)
    return selected


def _parse_exact_filters(raw_filters: str, all_columns: list[str]) -> dict[str, str]:
    if not raw_filters:
        return {}

    allow = set(all_columns)
    result: dict[str, str] = {}
    chunks = raw_filters.replace(";", ",").split(",")
    for chunk in chunks:
        pair = chunk.strip()
        if not pair:
            continue
        if "=" not in pair:
            continue
        key, value = pair.split("=", 1)
        key = key.strip()
        value = value.strip()
        if key and key in allow:
            result[key] = value
    return result


@router.get("/overview", response_model=ApiResponse)
def get_overview(db: Session = Depends(get_db)):
    auth_state = wechat_client.get_auth_state(db)
    mp_count = db.query(MPAccount).count()
    article_count = db.query(Article).count()
    latest_article = (
        db.query(Article)
        .order_by(Article.publish_ts.desc().nullslast(), Article.updated_at.desc())
        .first()
    )

    return ApiResponse(
        data={
            "auth": auth_state,
            "counts": {
                "mps": mp_count,
                "articles": article_count,
            },
            "latest_article": {
                "id": latest_article.id,
                "title": latest_article.title,
                "publish_ts": latest_article.publish_ts,
                "updated_at": latest_article.updated_at.isoformat(),
            }
            if latest_article
            else None,
        }
    )


@router.post("/quick-sync", response_model=ApiResponse)
def quick_sync(payload: QuickSyncRequest, db: Session = Depends(get_db)):
    try:
        search_result = wechat_client.search_mps(
            db,
            keyword=payload.keyword.strip(),
            offset=0,
            limit=20,
        )
    except WeChatAuthError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=500, detail=f"搜索公众号失败: {exc}") from exc

    mp_list = search_result.get("list", [])
    if not mp_list:
        raise HTTPException(status_code=404, detail="未找到公众号，请换个关键词")

    index = min(payload.pick_index, len(mp_list) - 1)
    target = mp_list[index]

    mp = article_service.create_or_update_mp(
        db,
        fakeid=target.get("fakeid") or "",
        nickname=target.get("nickname") or "未命名公众号",
        alias=target.get("alias"),
        avatar=target.get("avatar"),
        intro=target.get("intro"),
        biz=target.get("biz"),
    )

    try:
        sync_result = article_service.sync_mp_articles(
            db,
            mp=mp,
            pages=payload.pages,
            fetch_content=payload.fetch_content,
        )
    except WeChatAuthError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=500, detail=f"同步失败: {exc}") from exc

    return ApiResponse(
        data={
            "selected_mp": {
                "id": mp.id,
                "fakeid": mp.fakeid,
                "nickname": mp.nickname,
                "alias": mp.alias,
                "avatar": mp.avatar,
            },
            "sync": sync_result,
            "search_total": search_result.get("total", len(mp_list)),
        }
    )


@router.get("/db/tables", response_model=ApiResponse)
def list_db_tables(db: Session = Depends(get_db)):
    try:
        inspector = inspect(db.bind)
        names = inspector.get_table_names()
        metadata = MetaData()
        table_infos = []
        for name in names:
            table = Table(name, metadata, autoload_with=db.bind)
            try:
                row_count = db.execute(
                    select(func.count()).select_from(table)
                ).scalar_one()
            except Exception:
                row_count = 0

            table_infos.append(
                {
                    "name": name,
                    "comment": TABLE_COMMENTS.get(name, ""),
                    "row_count": row_count,
                    "columns": [col.name for col in table.columns],
                }
            )
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=500, detail=f"读取数据表失败: {exc}") from exc

    return ApiResponse(data={"tables": names, "table_infos": table_infos})


@router.get("/db/table/{table_name}", response_model=ApiResponse)
def read_db_table(
    table_name: str,
    offset: int = Query(0, ge=0),
    limit: int = Query(20, ge=1, le=200),
    keyword: str = Query("", description="模糊关键词搜索"),
    search_columns: str = Query("", description="搜索字段，逗号分隔"),
    exact_filters: str = Query("", description="精确筛选，格式 col=val,col2=val2"),
    db: Session = Depends(get_db),
):
    inspector = inspect(db.bind)
    table_names = inspector.get_table_names()
    if table_name not in table_names:
        raise HTTPException(status_code=404, detail="表不存在")

    try:
        metadata = MetaData()
        table = Table(table_name, metadata, autoload_with=db.bind)
        all_columns = [col.name for col in table.columns]

        selected_search_columns = _parse_search_columns(search_columns, all_columns)
        selected_exact_filters = _parse_exact_filters(exact_filters, all_columns)

        filters = []
        keyword = keyword.strip()
        if keyword:
            target_columns = selected_search_columns or all_columns
            like_exprs = [
                cast(table.c[col_name], String).ilike(f"%{keyword}%")
                for col_name in target_columns
            ]
            if like_exprs:
                filters.append(or_(*like_exprs))

        for col_name, value in selected_exact_filters.items():
            filters.append(cast(table.c[col_name], String) == value)

        where_clause = and_(*filters) if filters else None

        count_stmt = select(func.count()).select_from(table)
        if where_clause is not None:
            count_stmt = count_stmt.where(where_clause)
        total = db.execute(count_stmt).scalar_one()

        stmt = select(table)
        if where_clause is not None:
            stmt = stmt.where(where_clause)
        stmt = stmt.offset(offset).limit(limit)
        rows = db.execute(stmt).mappings().all()

        serialized_rows = []
        for row in rows:
            serialized_rows.append(
                {k: _serialize_value(v) for k, v in dict(row).items()}
            )

        return ApiResponse(
            data={
                "table": table_name,
                "table_comment": TABLE_COMMENTS.get(table_name, ""),
                "columns": all_columns,
                "column_comments": COLUMN_COMMENTS.get(table_name, {}),
                "total": total,
                "offset": offset,
                "limit": limit,
                "keyword": keyword,
                "search_columns": selected_search_columns,
                "exact_filters": selected_exact_filters,
                "rows": serialized_rows,
            }
        )
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=500, detail=f"读取表数据失败: {exc}") from exc


@router.get("/mcp/config", response_model=ApiResponse)
def get_mcp_config():
    if not settings.database_url.startswith("sqlite:///"):
        raise HTTPException(status_code=400, detail="当前 MCP 仅支持 SQLite 数据库")

    database_path = _resolve_sqlite_path()
    server_name = MCP_SERVER_NAME
    python_command = _resolve_python_command()
    args = ["-m", MCP_SERVER_MODULE, "--db-path", database_path]

    config = {
        "mcpServers": {
            server_name: {
                "command": python_command,
                "args": args,
            }
        }
    }

    return ApiResponse(
        data={
            "server_name": server_name,
            "database_path": database_path,
            "python_command": python_command,
            "launch_args": args,
            "tools": MCP_TOOL_DOCS,
            "install_steps": _build_mcp_install_steps(
                python_command=python_command,
                database_path=database_path,
            ),
            "config": config,
            "config_json": json.dumps(config, ensure_ascii=False, indent=2),
        }
    )


@router.post("/mcp/generate-file", response_model=ApiResponse)
def generate_mcp_file():
    data = get_mcp_config().data
    output_dir = Path(settings.data_dir) / "mcp"
    output_dir.mkdir(parents=True, exist_ok=True)

    output_file = output_dir / "we-mp-mini.mcp.json"
    output_file.write_text(data["config_json"], encoding="utf-8")

    return ApiResponse(
        data={
            "file_path": str(output_file.resolve()),
            "server_name": data["server_name"],
            "database_path": data["database_path"],
            "python_command": data.get("python_command", ""),
            "install_steps": data.get("install_steps", []),
            "tools": data.get("tools", []),
            "config_json": data["config_json"],
        }
    )
