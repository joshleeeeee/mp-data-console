import json
import shlex
import sys
from datetime import date, datetime, time, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import MetaData, String, Table, and_, cast, func, inspect, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.db import get_db
from app.models import Article, MPAccount
from app.schemas import (
    ApiResponse,
    AutoSyncEnabledUpdateRequest,
    DBRowCreateRequest,
    DBRowDeleteRequest,
    DBRowUpdateRequest,
    QuickSyncRequest,
)
from app.services.auto_sync_service import auto_sync_service
from app.services.article_service import article_service
from app.services.wechat_client import WeChatAuthError, wechat_client

router = APIRouter(prefix="/ops", tags=["ops"])

MCP_SERVER_NAME = "mp-data-console"
MCP_SERVER_MODULE = "app.mcp_server"
CLAUDE_CODE_MCP_DOCS_URL = "https://code.claude.com/docs/en/mcp"
CODEX_MCP_DOCS_URL = "https://developers.openai.com/codex/mcp/"
OPENCODE_CONFIG_SCHEMA_URL = "https://opencode.ai/config.json"
OPENCODE_CONFIG_DOCS_URL = "https://opencode.ai/docs/config/"
OPENCODE_MCP_DOCS_URL = "https://opencode.ai/docs/mcp-servers/"
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
    "capture_job_logs": "抓取任务事件日志（进度/错误/取消）",
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
        "is_favorite": "是否常用公众号",
        "use_count": "提交抓取任务次数",
        "last_used_at": "最近一次提交抓取时间",
        "last_sync_at": "最近同步时间",
        "auto_sync_enabled": "是否开启自动同步",
        "auto_sync_interval_minutes": "自动同步频率（分钟）",
        "auto_sync_lookback_days": "自动同步回看天数",
        "auto_sync_overlap_hours": "自动同步重叠小时数",
        "auto_sync_next_run_at": "自动同步下次执行时间",
        "auto_sync_last_success_at": "自动同步最近成功时间",
        "auto_sync_last_error": "自动同步最近错误",
        "auto_sync_consecutive_failures": "自动同步连续失败次数",
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
        "status": "任务状态（queued/running/canceling/success/failed/canceled）",
        "source": "任务来源（manual/scheduled/retry）",
        "pages_hint": "内部扫描页上限",
        "requested_count": "历史字段（已不作为抓取入参）",
        "start_ts": "时间范围起始（秒级时间戳）",
        "end_ts": "时间范围结束（秒级时间戳）",
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
    "capture_job_logs": {
        "id": "日志主键",
        "job_id": "关联任务 ID",
        "level": "日志级别（info/warn/error）",
        "message": "日志内容",
        "payload_json": "结构化上下文 JSON",
        "created_at": "记录时间",
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


def _resolve_project_root() -> str:
    return str(Path(__file__).resolve().parents[2])


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
        "将下方 Claude/Cursor MCP 配置粘贴到客户端",
        "如需更多说明，可参考 Claude Code MCP 官方文档",
    ]


def _build_opencode_install_steps(server_name: str) -> list[str]:
    return [
        "将下方 OpenCode 配置片段复制到 opencode.json（项目根）或 ~/.config/opencode/opencode.json（全局）",
        f"若已有 mcp 配置，请仅新增/合并 {server_name} 这一项，避免覆盖其他设置",
        "保存后重启 OpenCode，执行命令 opencode mcp list 确认已加载",
        "更多配置字段可参考 OpenCode 官方文档中的 Config 与 MCP Servers 章节",
    ]


def _build_codex_install_steps(server_name: str) -> list[str]:
    return [
        "将下方 Codex 配置片段复制到 ~/.codex/config.toml（全局）或 .codex/config.toml（trusted 项目）",
        f"若已有 mcp_servers 配置，请仅新增/合并 {server_name} 区块，避免覆盖其他设置",
        "保存后重启 Codex，可在 Codex TUI 中输入 /mcp 检查服务是否加载",
        "也可通过 codex mcp 命令管理 MCP 服务",
    ]


def _build_codex_config_toml(
    *,
    server_name: str,
    python_command: str,
    launch_args: list[str],
    project_root: str,
) -> str:
    command_literal = json.dumps(python_command, ensure_ascii=False)
    args_literal = json.dumps(launch_args, ensure_ascii=False)
    py_path_literal = json.dumps(project_root, ensure_ascii=False)

    return "\n".join(
        [
            f"[mcp_servers.{server_name}]",
            f"command = {command_literal}",
            f"args = {args_literal}",
            "",
            f"[mcp_servers.{server_name}.env]",
            f"PYTHONPATH = {py_path_literal}",
        ]
    )


def _build_codex_cli_add_command(
    *,
    server_name: str,
    python_command: str,
    launch_args: list[str],
    project_root: str,
) -> str:
    return shlex.join(
        [
            "codex",
            "mcp",
            "add",
            server_name,
            "--env",
            f"PYTHONPATH={project_root}",
            "--",
            python_command,
            *launch_args,
        ]
    )


def _build_opencode_config(
    *,
    server_name: str,
    python_command: str,
    launch_args: list[str],
    project_root: str,
) -> dict[str, Any]:
    return {
        "$schema": OPENCODE_CONFIG_SCHEMA_URL,
        "mcp": {
            server_name: {
                "type": "local",
                "enabled": True,
                "command": [python_command, *launch_args],
                "environment": {
                    "PYTHONPATH": project_root,
                },
            }
        },
    }


def _build_claude_cursor_config(
    *,
    server_name: str,
    python_command: str,
    launch_args: list[str],
) -> dict[str, Any]:
    return {
        "mcpServers": {
            server_name: {
                "command": python_command,
                "args": launch_args,
            }
        }
    }


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


def _load_table(db: Session, table_name: str) -> tuple[Table, list[str], list[str]]:
    inspector = inspect(db.bind)
    table_names = inspector.get_table_names()
    if table_name not in table_names:
        raise HTTPException(status_code=404, detail="表不存在")

    metadata = MetaData()
    table = Table(table_name, metadata, autoload_with=db.bind)
    all_columns = [column.name for column in table.columns]
    primary_keys = [column.name for column in table.primary_key.columns]
    return table, all_columns, primary_keys


def _column_python_type(column: Any) -> type[Any] | None:
    try:
        return column.type.python_type
    except Exception:  # noqa: BLE001
        return None


def _column_has_default(column: Any) -> bool:
    return column.default is not None or column.server_default is not None


def _column_is_int(column: Any) -> bool:
    return _column_python_type(column) is int


def _column_is_autoincrement_pk(column: Any) -> bool:
    autoincrement = getattr(column, "autoincrement", None)
    return bool(
        column.primary_key
        and _column_is_int(column)
        and autoincrement in (True, "auto")
    )


def _coerce_bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized in {"1", "true", "yes", "y", "on"}:
            return True
        if normalized in {"0", "false", "no", "n", "off"}:
            return False
    raise ValueError("布尔值仅支持 true/false/1/0")


def _coerce_datetime(value: Any) -> datetime:
    if isinstance(value, datetime):
        return value
    if isinstance(value, (int, float)):
        return datetime.fromtimestamp(value, tz=timezone.utc)
    if isinstance(value, str):
        raw = value.strip()
        if not raw:
            raise ValueError("时间不能为空")
        if raw.endswith("Z"):
            raw = f"{raw[:-1]}+00:00"
        return datetime.fromisoformat(raw)
    raise ValueError("时间格式不正确，需为 ISO 字符串")


def _coerce_date(value: Any) -> date:
    if isinstance(value, date) and not isinstance(value, datetime):
        return value
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, str):
        raw = value.strip()
        if not raw:
            raise ValueError("日期不能为空")
        return date.fromisoformat(raw)
    raise ValueError("日期格式不正确，需为 YYYY-MM-DD")


def _coerce_time(value: Any) -> time:
    if isinstance(value, time):
        return value
    if isinstance(value, str):
        raw = value.strip()
        if not raw:
            raise ValueError("时间不能为空")
        return time.fromisoformat(raw)
    raise ValueError("时间格式不正确，需为 HH:MM:SS")


def _coerce_column_value(column_name: str, column: Any, value: Any) -> Any:
    if value is None:
        return None

    python_type = _column_python_type(column)
    if python_type is None:
        return value

    try:
        if python_type is bool:
            return _coerce_bool(value)
        if python_type is int:
            if isinstance(value, bool):
                return int(value)
            if isinstance(value, int):
                return value
            if isinstance(value, float):
                if not value.is_integer():
                    raise ValueError("整数不能包含小数")
                return int(value)
            return int(str(value).strip())
        if python_type is float:
            return float(value)
        if python_type is Decimal:
            return Decimal(str(value))
        if python_type is datetime:
            return _coerce_datetime(value)
        if python_type is date:
            return _coerce_date(value)
        if python_type is time:
            return _coerce_time(value)
        if python_type in (dict, list):
            if isinstance(value, str):
                return json.loads(value)
            return value
        if python_type is str:
            return str(value)
        return value
    except (TypeError, ValueError, json.JSONDecodeError) as exc:
        raise ValueError(f"字段 {column_name} 的值格式无效: {exc}") from exc


def _normalize_row_values(table: Table, values: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(values, dict):
        raise HTTPException(status_code=400, detail="values 必须是对象")

    columns_by_name = {column.name: column for column in table.columns}
    unknown = sorted(set(values) - set(columns_by_name))
    if unknown:
        raise HTTPException(status_code=400, detail=f"字段不存在: {', '.join(unknown)}")

    normalized: dict[str, Any] = {}
    for name, value in values.items():
        column = columns_by_name[name]
        if value is None and not column.nullable:
            raise HTTPException(status_code=400, detail=f"字段 {name} 不能为空")
        try:
            normalized[name] = _coerce_column_value(name, column, value)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
    return normalized


def _build_pk_where_clause(
    table: Table,
    pk_payload: dict[str, Any],
) -> tuple[Any, dict[str, Any], list[str]]:
    pk_columns = [column.name for column in table.primary_key.columns]
    if not pk_columns:
        raise HTTPException(status_code=400, detail="当前表没有主键，无法执行更新/删除")

    if not isinstance(pk_payload, dict) or not pk_payload:
        raise HTTPException(status_code=400, detail="pk 参数不能为空")

    missing = [name for name in pk_columns if name not in pk_payload]
    if missing:
        raise HTTPException(
            status_code=400, detail=f"缺少主键字段: {', '.join(missing)}"
        )

    extra = sorted(set(pk_payload) - set(pk_columns))
    if extra:
        raise HTTPException(status_code=400, detail=f"无效主键字段: {', '.join(extra)}")

    pk_values = _normalize_row_values(
        table, {name: pk_payload[name] for name in pk_columns}
    )
    clauses = [table.c[name] == pk_values[name] for name in pk_columns]
    return and_(*clauses), pk_values, pk_columns


def _read_row_by_pk(
    db: Session,
    table: Table,
    pk_where_clause: Any,
) -> dict[str, Any] | None:
    row = db.execute(select(table).where(pk_where_clause)).mappings().first()
    if not row:
        return None
    return {key: _serialize_value(value) for key, value in dict(row).items()}


def _build_column_defs(table: Table) -> list[dict[str, Any]]:
    definitions = []
    for column in table.columns:
        definitions.append(
            {
                "name": column.name,
                "type": str(column.type),
                "nullable": bool(column.nullable),
                "primary_key": bool(column.primary_key),
                "has_default": _column_has_default(column),
                "autoincrement": _column_is_autoincrement_pk(column),
            }
        )
    return definitions


@router.get("/overview", response_model=ApiResponse)
def get_overview(db: Session = Depends(get_db)):
    auth_state = wechat_client.get_auth_state(db)
    mp_count = db.query(MPAccount).count()
    auto_sync_mp_count = (
        db.query(MPAccount)
        .filter(MPAccount.enabled.is_(True), MPAccount.auto_sync_enabled.is_(True))
        .count()
    )
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
                "auto_sync_mps": auto_sync_mp_count,
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


@router.get("/auto-sync/status", response_model=ApiResponse)
def get_auto_sync_status(db: Session = Depends(get_db)):
    return ApiResponse(data=auto_sync_service.get_status(db))


@router.patch("/auto-sync/enabled", response_model=ApiResponse)
def set_auto_sync_enabled(
    payload: AutoSyncEnabledUpdateRequest,
    db: Session = Depends(get_db),
):
    auto_sync_service.set_enabled(payload.enabled)
    if payload.enabled:
        auto_sync_service.sync_favorite_targets(db, run_immediately=True)
    return ApiResponse(data=auto_sync_service.get_status(db))


@router.post("/auto-sync/run-now", response_model=ApiResponse)
def run_auto_sync_now(
    mp_id: str = Query("", description="指定单个公众号 ID"),
    favorite_only: bool = Query(
        True, description="仅对常用公众号生效（未指定 mp_id 时）"
    ),
    limit: int = Query(20, ge=1, le=200),
    db: Session = Depends(get_db),
):
    data = auto_sync_service.queue_due_now(
        db,
        mp_id=mp_id,
        favorite_only=favorite_only,
        limit=limit,
    )
    return ApiResponse(data=data)


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
    try:
        table, all_columns, primary_keys = _load_table(db, table_name)

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
                "primary_keys": primary_keys,
                "column_defs": _build_column_defs(table),
                "rows": serialized_rows,
            }
        )
    except HTTPException:
        raise
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=500, detail=f"读取表数据失败: {exc}") from exc


@router.post("/db/table/{table_name}/row", response_model=ApiResponse)
def create_db_row(
    table_name: str,
    payload: DBRowCreateRequest,
    db: Session = Depends(get_db),
):
    try:
        table, _, primary_keys = _load_table(db, table_name)
        values = _normalize_row_values(table, payload.values)
        if not values:
            raise HTTPException(status_code=400, detail="新增数据不能为空")

        missing_required = []
        for column in table.columns:
            if column.name in values:
                continue
            if (
                column.nullable
                or _column_has_default(column)
                or _column_is_autoincrement_pk(column)
            ):
                continue
            missing_required.append(column.name)

        if missing_required:
            raise HTTPException(
                status_code=400,
                detail=f"缺少必填字段: {', '.join(missing_required)}",
            )

        result = db.execute(table.insert().values(**values))
        db.commit()

        pk_payload: dict[str, Any] = {}
        if primary_keys:
            inserted_primary_keys = list(result.inserted_primary_key or [])
            for index, key in enumerate(primary_keys):
                inserted_value = (
                    inserted_primary_keys[index]
                    if index < len(inserted_primary_keys)
                    else None
                )
                if inserted_value is not None:
                    pk_payload[key] = inserted_value
                elif key in values:
                    pk_payload[key] = values[key]

        row = None
        if primary_keys and len(pk_payload) == len(primary_keys):
            where_clause, _, _ = _build_pk_where_clause(table, pk_payload)
            row = _read_row_by_pk(db, table, where_clause)

        return ApiResponse(
            data={
                "table": table_name,
                "pk": pk_payload,
                "row": row,
            }
        )
    except HTTPException:
        db.rollback()
        raise
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(status_code=400, detail=f"写入失败: {exc}") from exc
    except Exception as exc:  # noqa: BLE001
        db.rollback()
        raise HTTPException(status_code=500, detail=f"新增失败: {exc}") from exc


@router.put("/db/table/{table_name}/row", response_model=ApiResponse)
def update_db_row(
    table_name: str,
    payload: DBRowUpdateRequest,
    db: Session = Depends(get_db),
):
    try:
        table, _, primary_keys = _load_table(db, table_name)
        if not primary_keys:
            raise HTTPException(status_code=400, detail="当前表没有主键，无法更新")

        if not payload.values:
            raise HTTPException(status_code=400, detail="更新数据不能为空")

        values = _normalize_row_values(table, payload.values)
        for pk_name in primary_keys:
            values.pop(pk_name, None)

        if not values:
            raise HTTPException(status_code=400, detail="没有可更新的字段")

        where_clause, pk_values, _ = _build_pk_where_clause(table, payload.pk)

        exists = _read_row_by_pk(db, table, where_clause)
        if not exists:
            raise HTTPException(status_code=404, detail="记录不存在")

        db.execute(table.update().where(where_clause).values(**values))
        db.commit()

        row = _read_row_by_pk(db, table, where_clause)
        return ApiResponse(
            data={
                "table": table_name,
                "pk": pk_values,
                "row": row,
            }
        )
    except HTTPException:
        db.rollback()
        raise
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(status_code=400, detail=f"更新失败: {exc}") from exc
    except Exception as exc:  # noqa: BLE001
        db.rollback()
        raise HTTPException(status_code=500, detail=f"更新失败: {exc}") from exc


@router.delete("/db/table/{table_name}/row", response_model=ApiResponse)
def delete_db_row(
    table_name: str,
    payload: DBRowDeleteRequest,
    db: Session = Depends(get_db),
):
    try:
        table, _, _ = _load_table(db, table_name)
        where_clause, pk_values, _ = _build_pk_where_clause(table, payload.pk)

        row = _read_row_by_pk(db, table, where_clause)
        if not row:
            raise HTTPException(status_code=404, detail="记录不存在")

        db.execute(table.delete().where(where_clause))
        db.commit()

        return ApiResponse(
            data={
                "table": table_name,
                "pk": pk_values,
                "deleted": row,
            }
        )
    except HTTPException:
        db.rollback()
        raise
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(status_code=400, detail=f"删除失败: {exc}") from exc
    except Exception as exc:  # noqa: BLE001
        db.rollback()
        raise HTTPException(status_code=500, detail=f"删除失败: {exc}") from exc


@router.get("/mcp/config", response_model=ApiResponse)
def get_mcp_config():
    if not settings.database_url.startswith("sqlite:///"):
        raise HTTPException(status_code=400, detail="当前 MCP 仅支持 SQLite 数据库")

    database_path = _resolve_sqlite_path()
    server_name = MCP_SERVER_NAME
    python_command = _resolve_python_command()
    launch_args = ["-m", MCP_SERVER_MODULE, "--db-path", database_path]
    project_root = _resolve_project_root()

    claude_config = _build_claude_cursor_config(
        server_name=server_name,
        python_command=python_command,
        launch_args=launch_args,
    )
    opencode_config = _build_opencode_config(
        server_name=server_name,
        python_command=python_command,
        launch_args=launch_args,
        project_root=project_root,
    )
    codex_config_toml = _build_codex_config_toml(
        server_name=server_name,
        python_command=python_command,
        launch_args=launch_args,
        project_root=project_root,
    )
    codex_cli_add_command = _build_codex_cli_add_command(
        server_name=server_name,
        python_command=python_command,
        launch_args=launch_args,
        project_root=project_root,
    )
    claude_install_steps = _build_mcp_install_steps(
        python_command=python_command,
        database_path=database_path,
    )
    opencode_install_steps = _build_opencode_install_steps(server_name=server_name)
    codex_install_steps = _build_codex_install_steps(server_name=server_name)

    return ApiResponse(
        data={
            "server_name": server_name,
            "database_path": database_path,
            "project_root": project_root,
            "python_command": python_command,
            "launch_args": launch_args,
            "tools": MCP_TOOL_DOCS,
            "install_steps": claude_install_steps,
            "claude_install_steps": claude_install_steps,
            "opencode_install_steps": opencode_install_steps,
            "codex_install_steps": codex_install_steps,
            "config": claude_config,
            "claude_config": claude_config,
            "opencode_config": opencode_config,
            "config_json": json.dumps(claude_config, ensure_ascii=False, indent=2),
            "claude_config_json": json.dumps(
                claude_config, ensure_ascii=False, indent=2
            ),
            "opencode_config_json": json.dumps(
                opencode_config, ensure_ascii=False, indent=2
            ),
            "codex_config_toml": codex_config_toml,
            "codex_cli_add_command": codex_cli_add_command,
            "claude_mcp_docs_url": CLAUDE_CODE_MCP_DOCS_URL,
            "codex_mcp_docs_url": CODEX_MCP_DOCS_URL,
            "opencode_docs_url": OPENCODE_CONFIG_DOCS_URL,
            "opencode_mcp_docs_url": OPENCODE_MCP_DOCS_URL,
        }
    )


@router.post("/mcp/generate-file", response_model=ApiResponse)
def generate_mcp_file():
    data = get_mcp_config().data
    output_dir = Path(settings.data_dir) / "mcp"
    output_dir.mkdir(parents=True, exist_ok=True)

    output_file = output_dir / "we-mp-mini.mcp.json"
    output_file.write_text(data["claude_config_json"], encoding="utf-8")
    opencode_output_file = output_dir / "we-mp-mini.opencode.json"
    opencode_output_file.write_text(data["opencode_config_json"], encoding="utf-8")
    codex_output_file = output_dir / "we-mp-mini.codex.toml"
    codex_output_file.write_text(data["codex_config_toml"], encoding="utf-8")

    return ApiResponse(
        data={
            "file_path": str(output_file.resolve()),
            "opencode_file_path": str(opencode_output_file.resolve()),
            "codex_file_path": str(codex_output_file.resolve()),
            "server_name": data["server_name"],
            "database_path": data["database_path"],
            "python_command": data.get("python_command", ""),
            "install_steps": data.get("install_steps", []),
            "claude_install_steps": data.get("claude_install_steps", []),
            "opencode_install_steps": data.get("opencode_install_steps", []),
            "codex_install_steps": data.get("codex_install_steps", []),
            "tools": data.get("tools", []),
            "config_json": data["config_json"],
            "claude_config_json": data["claude_config_json"],
            "opencode_config_json": data["opencode_config_json"],
            "codex_config_toml": data["codex_config_toml"],
            "codex_cli_add_command": data.get("codex_cli_add_command", ""),
            "claude_mcp_docs_url": data.get(
                "claude_mcp_docs_url", CLAUDE_CODE_MCP_DOCS_URL
            ),
            "codex_mcp_docs_url": data.get("codex_mcp_docs_url", CODEX_MCP_DOCS_URL),
            "opencode_docs_url": data.get(
                "opencode_docs_url", OPENCODE_CONFIG_DOCS_URL
            ),
            "opencode_mcp_docs_url": data.get(
                "opencode_mcp_docs_url", OPENCODE_MCP_DOCS_URL
            ),
        }
    )
