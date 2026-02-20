from __future__ import annotations

import argparse
import re
import sqlite3
from pathlib import Path
from typing import Any

from mcp.server.fastmcp import FastMCP

from app.core.config import settings

SERVER_NAME = "mp-data-console"
DEFAULT_LIMIT = 20
MAX_LIMIT = 100
DEFAULT_MAX_CHARS = 12000
MAX_TEXT_CHARS = 200000


def _resolve_sqlite_path(raw_path: str = "") -> Path:
    candidate = raw_path.strip()
    if candidate:
        return Path(candidate).expanduser().resolve()

    db_url = settings.database_url
    if not db_url.startswith("sqlite:///"):
        raise ValueError("Only sqlite:/// database_url is supported for MCP server")

    db_path = Path(db_url.removeprefix("sqlite:///"))
    if not db_path.is_absolute():
        db_path = (Path.cwd() / db_path).resolve()
    return db_path


def _ensure_database_ready(db_path: Path) -> None:
    if not db_path.exists():
        raise FileNotFoundError(f"SQLite file not found: {db_path}")

    with sqlite3.connect(str(db_path)) as conn:
        row = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='articles'"
        ).fetchone()
        if row is None:
            raise RuntimeError(
                "Table 'articles' not found. Please run capture first to initialize DB schema."
            )


def _open_connection(db_path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(str(db_path), check_same_thread=False)
    conn.row_factory = sqlite3.Row
    return conn


def _safe_limit(raw_limit: int, default: int = DEFAULT_LIMIT) -> int:
    try:
        limit = int(raw_limit)
    except (TypeError, ValueError):
        return default
    return max(1, min(MAX_LIMIT, limit))


def _safe_offset(raw_offset: int) -> int:
    try:
        offset = int(raw_offset)
    except (TypeError, ValueError):
        return 0
    return max(0, offset)


def _safe_max_chars(raw_max_chars: int) -> int:
    try:
        max_chars = int(raw_max_chars)
    except (TypeError, ValueError):
        return DEFAULT_MAX_CHARS
    return max(200, min(MAX_TEXT_CHARS, max_chars))


def _compact_whitespace(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()


def _build_preview(text: str, keyword: str, max_chars: int = 180) -> str:
    normalized = _compact_whitespace(text)
    if not normalized:
        return ""

    trimmed_keyword = keyword.strip().lower()
    if not trimmed_keyword:
        return normalized[:max_chars]

    index = normalized.lower().find(trimmed_keyword)
    if index < 0:
        return normalized[:max_chars]

    start = max(0, index - max_chars // 3)
    end = min(len(normalized), start + max_chars)
    preview = normalized[start:end]

    if start > 0:
        preview = f"...{preview}"
    if end < len(normalized):
        preview = f"{preview}..."
    return preview


def _strip_html(raw_html: str) -> str:
    without_script = re.sub(
        r"<script[\s\S]*?</script>|<style[\s\S]*?</style>",
        " ",
        raw_html,
        flags=re.IGNORECASE,
    )
    plain = re.sub(r"<[^>]+>", " ", without_script)
    return _compact_whitespace(plain)


def _to_mp_meta(row: sqlite3.Row) -> dict[str, Any]:
    return {
        "id": row["id"],
        "nickname": row["nickname"],
        "alias": row["alias"],
        "fakeid": row["fakeid"],
    }


def build_server(db_path: Path) -> FastMCP:
    server = FastMCP(SERVER_NAME)

    @server.tool(
        description=(
            "Return counts for MP accounts/articles and latest captured article metadata."
        )
    )
    def db_overview() -> dict[str, Any]:
        with _open_connection(db_path) as conn:
            mp_total = conn.execute("SELECT COUNT(1) FROM mps").fetchone()[0]
            article_total = conn.execute("SELECT COUNT(1) FROM articles").fetchone()[0]
            article_with_text = conn.execute(
                "SELECT COUNT(1) FROM articles WHERE COALESCE(TRIM(content_text), '') != ''"
            ).fetchone()[0]
            latest_row = conn.execute(
                """
                SELECT id, title, mp_id, url, publish_ts, updated_at
                FROM articles
                ORDER BY COALESCE(publish_ts, 0) DESC, updated_at DESC
                LIMIT 1
                """
            ).fetchone()

        latest_article = None
        if latest_row is not None:
            latest_article = {
                "id": latest_row["id"],
                "title": latest_row["title"],
                "mp_id": latest_row["mp_id"],
                "url": latest_row["url"],
                "publish_ts": latest_row["publish_ts"],
                "updated_at": latest_row["updated_at"],
            }

        return {
            "database_path": str(db_path),
            "counts": {
                "mps": mp_total,
                "articles": article_total,
                "articles_with_text": article_with_text,
            },
            "latest_article": latest_article,
        }

    @server.tool(
        description=(
            "List MP accounts in database, with article counts and latest capture info."
        )
    )
    def list_mps(
        keyword: str = "",
        only_with_articles: bool = True,
        limit: int = DEFAULT_LIMIT,
        offset: int = 0,
    ) -> dict[str, Any]:
        safe_limit = _safe_limit(limit)
        safe_offset = _safe_offset(offset)
        keyword = keyword.strip()

        where_clauses: list[str] = []
        params: list[Any] = []
        if keyword:
            like_keyword = f"%{keyword}%"
            where_clauses.append(
                "("
                "m.id = ? OR "
                "COALESCE(m.nickname, '') LIKE ? OR "
                "COALESCE(m.alias, '') LIKE ? OR "
                "COALESCE(m.fakeid, '') LIKE ?"
                ")"
            )
            params.extend([keyword, like_keyword, like_keyword, like_keyword])

        where_sql = ""
        if where_clauses:
            where_sql = f"WHERE {' AND '.join(where_clauses)}"

        having_sql = ""
        if only_with_articles:
            having_sql = "HAVING COUNT(a.id) > 0"

        base_group_sql = f"""
            FROM mps m
            LEFT JOIN articles a ON a.mp_id = m.id
            {where_sql}
            GROUP BY m.id
            {having_sql}
        """

        count_sql = f"SELECT COUNT(1) FROM (SELECT m.id {base_group_sql}) tmp"
        query_sql = f"""
            SELECT
                m.id,
                m.fakeid,
                m.nickname,
                m.alias,
                m.last_sync_at,
                m.updated_at,
                COUNT(a.id) AS article_count,
                SUM(
                    CASE
                        WHEN COALESCE(TRIM(a.content_text), '') != '' THEN 1
                        ELSE 0
                    END
                ) AS article_with_text_count,
                MAX(COALESCE(a.publish_ts, 0)) AS latest_publish_ts
            {base_group_sql}
            ORDER BY article_count DESC, m.updated_at DESC
            LIMIT ? OFFSET ?
        """

        with _open_connection(db_path) as conn:
            total = conn.execute(count_sql, params).fetchone()[0]
            rows = conn.execute(
                query_sql, [*params, safe_limit, safe_offset]
            ).fetchall()

        items: list[dict[str, Any]] = []
        for row in rows:
            items.append(
                {
                    "id": row["id"],
                    "fakeid": row["fakeid"],
                    "nickname": row["nickname"],
                    "alias": row["alias"],
                    "article_count": row["article_count"],
                    "article_with_text_count": row["article_with_text_count"],
                    "latest_publish_ts": row["latest_publish_ts"],
                    "last_sync_at": row["last_sync_at"],
                    "updated_at": row["updated_at"],
                }
            )

        return {
            "total": total,
            "offset": safe_offset,
            "limit": safe_limit,
            "keyword": keyword,
            "only_with_articles": only_with_articles,
            "items": items,
        }

    @server.tool(
        description=(
            "List articles by MP name/id, useful for checking what is stored for one account."
        )
    )
    def list_articles_by_mp(
        mp_keyword: str = "",
        mp_id: str = "",
        article_keyword: str = "",
        only_with_text: bool = False,
        limit: int = DEFAULT_LIMIT,
        offset: int = 0,
    ) -> dict[str, Any]:
        safe_limit = _safe_limit(limit)
        safe_offset = _safe_offset(offset)
        mp_keyword = mp_keyword.strip()
        mp_id = mp_id.strip()
        article_keyword = article_keyword.strip()

        if not mp_id and not mp_keyword:
            raise ValueError("Please provide mp_id or mp_keyword")

        with _open_connection(db_path) as conn:
            matched_mps: list[sqlite3.Row] = []
            if mp_id:
                matched_mps = conn.execute(
                    """
                    SELECT id, nickname, alias, fakeid
                    FROM mps
                    WHERE id = ?
                    LIMIT 1
                    """,
                    [mp_id],
                ).fetchall()
            else:
                like_mp = f"%{mp_keyword}%"
                matched_mps = conn.execute(
                    """
                    SELECT id, nickname, alias, fakeid
                    FROM mps
                    WHERE
                        COALESCE(nickname, '') LIKE ?
                        OR COALESCE(alias, '') LIKE ?
                        OR COALESCE(fakeid, '') LIKE ?
                        OR id = ?
                    ORDER BY updated_at DESC, created_at DESC
                    LIMIT 50
                    """,
                    [like_mp, like_mp, like_mp, mp_keyword],
                ).fetchall()

            if not matched_mps:
                raise ValueError("No MP account matched the given mp_id/mp_keyword")

            mp_ids = [row["id"] for row in matched_mps]
            placeholders = ", ".join(["?"] * len(mp_ids))

            where_clauses = [f"a.mp_id IN ({placeholders})"]
            params: list[Any] = [*mp_ids]

            if article_keyword:
                like_article = f"%{article_keyword}%"
                where_clauses.append(
                    "("
                    "a.title LIKE ? OR "
                    "COALESCE(a.digest, '') LIKE ? OR "
                    "COALESCE(a.content_text, '') LIKE ?"
                    ")"
                )
                params.extend([like_article, like_article, like_article])

            if only_with_text:
                where_clauses.append("COALESCE(TRIM(a.content_text), '') != ''")

            where_sql = f"WHERE {' AND '.join(where_clauses)}"

            count_sql = f"SELECT COUNT(1) FROM articles a {where_sql}"
            query_sql = f"""
                SELECT
                    a.id,
                    a.mp_id,
                    a.title,
                    a.url,
                    a.author,
                    a.publish_ts,
                    a.updated_at,
                    a.content_text,
                    m.nickname AS mp_nickname,
                    m.alias AS mp_alias
                FROM articles a
                LEFT JOIN mps m ON m.id = a.mp_id
                {where_sql}
                ORDER BY COALESCE(a.publish_ts, 0) DESC, a.updated_at DESC
                LIMIT ? OFFSET ?
            """

            total = conn.execute(count_sql, params).fetchone()[0]
            rows = conn.execute(
                query_sql, [*params, safe_limit, safe_offset]
            ).fetchall()

        items: list[dict[str, Any]] = []
        for row in rows:
            raw_text = row["content_text"] or ""
            items.append(
                {
                    "id": row["id"],
                    "title": row["title"],
                    "mp_id": row["mp_id"],
                    "mp_nickname": row["mp_nickname"],
                    "mp_alias": row["mp_alias"],
                    "author": row["author"],
                    "url": row["url"],
                    "publish_ts": row["publish_ts"],
                    "updated_at": row["updated_at"],
                    "has_content_text": bool(_compact_whitespace(raw_text)),
                    "text_preview": _build_preview(raw_text, article_keyword),
                }
            )

        return {
            "matched_mp_count": len(matched_mps),
            "matched_mps": [_to_mp_meta(row) for row in matched_mps],
            "total": total,
            "offset": safe_offset,
            "limit": safe_limit,
            "mp_id": mp_id,
            "mp_keyword": mp_keyword,
            "article_keyword": article_keyword,
            "only_with_text": only_with_text,
            "items": items,
        }

    @server.tool(
        description=(
            "Search captured articles by title/content text and return text previews."
        )
    )
    def search_articles(
        keyword: str = "",
        mp_keyword: str = "",
        only_with_text: bool = True,
        limit: int = DEFAULT_LIMIT,
        offset: int = 0,
    ) -> dict[str, Any]:
        safe_limit = _safe_limit(limit)
        safe_offset = _safe_offset(offset)
        keyword = keyword.strip()
        mp_keyword = mp_keyword.strip()

        where_clauses: list[str] = []
        params: list[Any] = []

        if keyword:
            like_keyword = f"%{keyword}%"
            where_clauses.append(
                "("
                "a.title LIKE ? OR "
                "COALESCE(a.content_text, '') LIKE ? OR "
                "COALESCE(a.digest, '') LIKE ?"
                ")"
            )
            params.extend([like_keyword, like_keyword, like_keyword])

        if mp_keyword:
            like_mp = f"%{mp_keyword}%"
            where_clauses.append(
                "("
                "a.mp_id = ? OR "
                "COALESCE(m.nickname, '') LIKE ? OR "
                "COALESCE(m.alias, '') LIKE ?"
                ")"
            )
            params.extend([mp_keyword, like_mp, like_mp])

        if only_with_text:
            where_clauses.append("COALESCE(TRIM(a.content_text), '') != ''")

        where_sql = ""
        if where_clauses:
            where_sql = f"WHERE {' AND '.join(where_clauses)}"

        from_sql = "FROM articles a LEFT JOIN mps m ON m.id = a.mp_id"
        count_sql = f"SELECT COUNT(1) {from_sql} {where_sql}"
        query_sql = f"""
            SELECT
                a.id,
                a.mp_id,
                a.title,
                a.url,
                a.author,
                a.publish_ts,
                a.updated_at,
                a.content_text,
                m.nickname AS mp_nickname,
                m.alias AS mp_alias
            {from_sql}
            {where_sql}
            ORDER BY COALESCE(a.publish_ts, 0) DESC, a.updated_at DESC
            LIMIT ? OFFSET ?
        """

        with _open_connection(db_path) as conn:
            total = conn.execute(count_sql, params).fetchone()[0]
            rows = conn.execute(
                query_sql, [*params, safe_limit, safe_offset]
            ).fetchall()

        items: list[dict[str, Any]] = []
        for row in rows:
            raw_text = row["content_text"] or ""
            items.append(
                {
                    "id": row["id"],
                    "title": row["title"],
                    "mp_id": row["mp_id"],
                    "mp_nickname": row["mp_nickname"],
                    "mp_alias": row["mp_alias"],
                    "author": row["author"],
                    "url": row["url"],
                    "publish_ts": row["publish_ts"],
                    "updated_at": row["updated_at"],
                    "has_content_text": bool(_compact_whitespace(raw_text)),
                    "text_preview": _build_preview(raw_text, keyword),
                }
            )

        return {
            "total": total,
            "offset": safe_offset,
            "limit": safe_limit,
            "keyword": keyword,
            "mp_keyword": mp_keyword,
            "only_with_text": only_with_text,
            "items": items,
        }

    @server.tool(
        description=("Read full text from one captured article by article_id or url.")
    )
    def get_article_text(
        article_id: str = "",
        url: str = "",
        max_chars: int = DEFAULT_MAX_CHARS,
    ) -> dict[str, Any]:
        article_id = article_id.strip()
        url = url.strip()
        safe_max_chars = _safe_max_chars(max_chars)

        if not article_id and not url:
            raise ValueError("Please provide article_id or url")

        base_sql = """
            SELECT
                a.id,
                a.mp_id,
                a.title,
                a.url,
                a.author,
                a.publish_ts,
                a.updated_at,
                a.content_text,
                a.content_html,
                m.nickname AS mp_nickname,
                m.alias AS mp_alias
            FROM articles a
            LEFT JOIN mps m ON m.id = a.mp_id
        """

        with _open_connection(db_path) as conn:
            row = None
            if article_id:
                row = conn.execute(
                    f"{base_sql} WHERE a.id = ? LIMIT 1", [article_id]
                ).fetchone()
            if row is None and url:
                row = conn.execute(
                    f"{base_sql} WHERE a.url = ? LIMIT 1", [url]
                ).fetchone()

        if row is None:
            raise ValueError("Article not found")

        text = _compact_whitespace(row["content_text"] or "")
        source = "content_text"
        if not text:
            text = _strip_html(row["content_html"] or "")
            source = "content_html"

        truncated = len(text) > safe_max_chars
        text_output = text[:safe_max_chars] if truncated else text

        return {
            "id": row["id"],
            "title": row["title"],
            "mp_id": row["mp_id"],
            "mp_nickname": row["mp_nickname"],
            "mp_alias": row["mp_alias"],
            "author": row["author"],
            "url": row["url"],
            "publish_ts": row["publish_ts"],
            "updated_at": row["updated_at"],
            "text_source": source,
            "text_length": len(text),
            "max_chars": safe_max_chars,
            "truncated": truncated,
            "content_text": text_output,
        }

    return server


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="MCP server for reading captured WeChat article texts from SQLite"
    )
    parser.add_argument(
        "--db-path",
        default="",
        help="SQLite file path. Defaults to DATABASE_URL from project settings.",
    )
    return parser.parse_args()


def main() -> None:
    args = _parse_args()
    db_path = _resolve_sqlite_path(args.db_path)
    _ensure_database_ready(db_path)

    server = build_server(db_path)
    server.run(transport="stdio")


if __name__ == "__main__":
    main()
