import base64
import hashlib
import json
import re
import time
from collections.abc import Callable
from datetime import datetime, timezone
from typing import Any

from bs4 import BeautifulSoup
from sqlalchemy import desc, or_
from sqlalchemy.orm import Session

from app.core.config import settings
from app.models import Article, MPAccount
from app.services.wechat_client import WeChatAuthError, WeChatClient, wechat_client


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class ArticleService:
    def __init__(self, client: WeChatClient):
        self.client = client

    @staticmethod
    def _build_mp_id(fakeid: str, biz: str | None = None) -> str:
        if biz:
            try:
                decoded = base64.b64decode(biz).decode("utf-8")
                if decoded:
                    return f"MP_WXS_{decoded}"
            except Exception:
                pass

        digest = hashlib.md5(fakeid.encode("utf-8"), usedforsecurity=False).hexdigest()[
            :12
        ]
        return f"MP_FAKE_{digest}"

    def create_or_update_mp(
        self,
        db: Session,
        fakeid: str,
        nickname: str,
        alias: str | None = None,
        avatar: str | None = None,
        intro: str | None = None,
        biz: str | None = None,
    ) -> MPAccount:
        mp = db.query(MPAccount).filter(MPAccount.fakeid == fakeid).first()
        if not mp and biz:
            mp = db.query(MPAccount).filter(MPAccount.biz == biz).first()

        if not mp:
            mp = MPAccount(
                id=self._build_mp_id(fakeid, biz),
                fakeid=fakeid,
                biz=biz,
                nickname=nickname,
                alias=alias,
                avatar=avatar,
                intro=intro,
            )
            db.add(mp)
        else:
            mp.nickname = nickname
            mp.alias = alias
            mp.avatar = avatar
            mp.intro = intro
            mp.biz = biz or mp.biz

        db.commit()
        db.refresh(mp)
        return mp

    def list_mps(
        self,
        db: Session,
        offset: int = 0,
        limit: int = 20,
        favorite_only: bool = False,
    ) -> tuple[list[MPAccount], int]:
        query = db.query(MPAccount)
        if favorite_only:
            query = query.filter(MPAccount.is_favorite.is_(True))
        total = query.count()
        rows = (
            query.order_by(
                desc(MPAccount.is_favorite),
                desc(MPAccount.last_used_at),
                desc(MPAccount.updated_at),
            )
            .offset(offset)
            .limit(limit)
            .all()
        )
        return rows, total

    def get_mp(self, db: Session, mp_id: str) -> MPAccount | None:
        return db.query(MPAccount).filter(MPAccount.id == mp_id).first()

    def set_mp_favorite(
        self, db: Session, mp_id: str, is_favorite: bool
    ) -> MPAccount | None:
        mp = self.get_mp(db, mp_id)
        if not mp:
            return None

        mp.is_favorite = bool(is_favorite)
        mp.updated_at = utcnow()
        db.add(mp)
        db.commit()
        db.refresh(mp)
        return mp

    def mark_mp_used(self, db: Session, mp: MPAccount) -> MPAccount:
        mp.use_count = max(0, int(mp.use_count or 0)) + 1
        mp.last_used_at = utcnow()
        mp.updated_at = utcnow()
        db.add(mp)
        db.commit()
        db.refresh(mp)
        return mp

    @staticmethod
    def _extract_from_publish_payload(payload: dict[str, Any]) -> list[dict[str, Any]]:
        publish_page = payload.get("publish_page")
        if isinstance(publish_page, str):
            try:
                publish_page = json.loads(publish_page)
            except json.JSONDecodeError:
                publish_page = {}
        if not isinstance(publish_page, dict):
            return []

        result: list[dict[str, Any]] = []
        for item in publish_page.get("publish_list", []):
            publish_info = item.get("publish_info")
            if isinstance(publish_info, str):
                try:
                    publish_info = json.loads(publish_info)
                except json.JSONDecodeError:
                    publish_info = {}
            if not isinstance(publish_info, dict):
                continue
            for article in publish_info.get("appmsgex", []):
                result.append(article)
        return result

    @staticmethod
    def _extract_from_appmsg_payload(payload: dict[str, Any]) -> list[dict[str, Any]]:
        result = []
        for item in payload.get("app_msg_list", []):
            result.append(item)
        return result

    @staticmethod
    def _resolve_aid(item: dict[str, Any], url: str) -> str:
        aid = item.get("aid")
        if aid:
            return str(aid)
        digest = hashlib.md5(url.encode("utf-8"), usedforsecurity=False).hexdigest()
        return digest

    @staticmethod
    def _safe_int(value: Any) -> int | None:
        try:
            if value is None:
                return None
            return int(value)
        except (TypeError, ValueError):
            return None

    def _upsert_article_from_item(
        self, db: Session, mp: MPAccount, item: dict[str, Any]
    ) -> tuple[Article, bool]:
        url = item.get("link") or item.get("url") or ""
        if not url:
            raise ValueError("文章链接为空")

        aid = self._resolve_aid(item, url)
        article_id = f"{mp.id}-{aid}"

        article = (
            db.query(Article)
            .filter(or_(Article.id == article_id, Article.url == url))
            .first()
        )
        created = article is None
        if article is None:
            article = Article(
                id=article_id,
                aid=aid,
                mp_id=mp.id,
                url=url,
                title=item.get("title") or "",
            )
            db.add(article)

        article.mp_id = mp.id
        article.aid = aid
        article.title = item.get("title") or article.title or ""
        article.cover_url = (
            item.get("cover") or item.get("pic_url") or article.cover_url
        )
        article.digest = item.get("digest") or article.digest
        article.author = item.get("author") or article.author
        article.publish_ts = self._safe_int(
            item.get("update_time") or item.get("create_time")
        )
        article.raw_json = json.dumps(item, ensure_ascii=False)
        article.updated_at = utcnow()

        return article, created

    def sync_mp_articles(
        self,
        db: Session,
        mp: MPAccount,
        pages: int = 1,
        fetch_content: bool = True,
        target_count: int | None = None,
        progress_callback: Callable[[dict[str, Any]], None] | None = None,
    ) -> dict[str, Any]:
        created_count = 0
        updated_count = 0
        content_updated_count = 0

        base_pages = max(1, int(pages))
        requested_unique = (
            max(1, int(target_count)) if target_count is not None else base_pages * 5
        )

        if target_count is not None:
            estimated_pages = max(1, (requested_unique + 4) // 5)
            max_pages = min(300, max(base_pages, estimated_pages * 4))
        else:
            max_pages = base_pages

        scanned_pages = 0
        duplicates_skipped = 0
        seen_keys: set[str] = set()
        reached_target = False

        def emit_progress() -> None:
            if not progress_callback:
                return
            try:
                progress_callback(
                    {
                        "created": created_count,
                        "updated": updated_count,
                        "content_updated": content_updated_count,
                        "duplicates_skipped": duplicates_skipped,
                        "scanned_pages": scanned_pages,
                        "max_pages": max_pages,
                        "requested_unique": requested_unique,
                        "reached_target": reached_target,
                    }
                )
            except Exception:
                return

        for page_index in range(max_pages):
            scanned_pages = page_index + 1
            begin = page_index * 5

            payload = self.client.fetch_publish_page(
                db, mp.fakeid, begin=begin, count=5
            )
            records = self._extract_from_publish_payload(payload)
            if not records:
                fallback = self.client.fetch_appmsg_page(
                    db, mp.fakeid, begin=begin, count=5
                )
                records = self._extract_from_appmsg_payload(fallback)

            if not records:
                break

            for item in records:
                url = (item.get("link") or item.get("url") or "").strip()
                if not url:
                    continue

                seen_markers = [f"url:{url}"]
                aid = item.get("aid")
                if aid:
                    seen_markers.append(f"aid:{aid}")

                if any(marker in seen_keys for marker in seen_markers):
                    duplicates_skipped += 1
                    continue

                for marker in seen_markers:
                    seen_keys.add(marker)

                article, created = self._upsert_article_from_item(db, mp, item)
                if created:
                    created_count += 1
                else:
                    updated_count += 1

                if fetch_content and article.url:
                    detail = self.fetch_article_detail(db, article.url)
                    article.content_html = detail.get("content_html")
                    article.content_text = detail.get("content_text")
                    article.cover_url = detail.get("cover_url") or article.cover_url
                    article.digest = detail.get("digest") or article.digest
                    article.author = detail.get("author") or article.author
                    detail_publish_ts = self._safe_int(detail.get("publish_ts"))
                    article.publish_ts = detail_publish_ts or article.publish_ts
                    article.images_json = json.dumps(
                        detail.get("images", []), ensure_ascii=False
                    )
                    article.updated_at = utcnow()
                    content_updated_count += 1

                if created_count >= requested_unique:
                    reached_target = True
                    break

            db.commit()
            emit_progress()

            if reached_target:
                break

            time.sleep(0.4)

        mp.last_sync_at = utcnow()
        mp.updated_at = utcnow()
        db.commit()

        return {
            "mp_id": mp.id,
            "created": created_count,
            "updated": updated_count,
            "content_updated": content_updated_count,
            "pages": base_pages,
            "scanned_pages": scanned_pages,
            "max_pages": max_pages,
            "requested_unique": requested_unique,
            "reached_target": reached_target,
            "duplicates_skipped": duplicates_skipped,
        }

    def fetch_article_detail(self, db: Session, article_url: str) -> dict[str, Any]:
        html = self.client.fetch_article_html(db, article_url)
        if "当前环境异常，完成验证后即可继续访问" in html:
            fallback_html = self._fetch_article_html_playwright(db, article_url)
            if fallback_html:
                html = fallback_html
        return self._parse_article_html(html)

    def _fetch_article_html_playwright(
        self, db: Session, article_url: str
    ) -> str | None:
        try:
            from playwright.sync_api import sync_playwright
        except Exception:
            return None

        session, _ = self.client.ensure_login(db)
        pw_cookies = []
        for cookie in session.cookies:
            domain = cookie.domain or ".weixin.qq.com"
            pw_cookies.append(
                {
                    "name": cookie.name,
                    "value": cookie.value,
                    "domain": domain,
                    "path": cookie.path or "/",
                    "secure": bool(cookie.secure),
                }
            )

        browser_name = settings.playwright_browser.strip().lower()

        try:
            with sync_playwright() as pw:
                browser_factory = getattr(pw, browser_name, pw.chromium)
                browser = browser_factory.launch(headless=settings.playwright_headless)
                context = browser.new_context(user_agent=settings.user_agent)
                if pw_cookies:
                    context.add_cookies(pw_cookies)
                page = context.new_page()
                page.goto(
                    article_url,
                    wait_until="domcontentloaded",
                    timeout=settings.playwright_timeout_ms,
                )
                page.wait_for_timeout(1200)
                html = page.content()
                browser.close()
                return html
        except Exception:
            return None

    @staticmethod
    def _parse_article_html(html: str) -> dict[str, Any]:
        soup = BeautifulSoup(html or "", "lxml")

        content_node = soup.select_one("#js_content") or soup.select_one("#js_article")
        if content_node is not None:
            for bad in content_node.find_all(["script", "style"]):
                bad.decompose()

            # 微信正文常见默认隐藏样式：visibility:hidden + opacity:0
            nodes = [content_node, *content_node.find_all(True)]
            for node in nodes:
                style = node.get("style")
                if style:
                    cleaned_style = re.sub(
                        r"visibility\s*:\s*hidden\s*;?", "", style, flags=re.IGNORECASE
                    )
                    cleaned_style = re.sub(
                        r"opacity\s*:\s*0(?:\.0+)?\s*;?",
                        "",
                        cleaned_style,
                        flags=re.IGNORECASE,
                    )
                    cleaned_style = re.sub(
                        r"display\s*:\s*none\s*;?",
                        "",
                        cleaned_style,
                        flags=re.IGNORECASE,
                    )
                    cleaned_style = re.sub(r";\s*;", ";", cleaned_style).strip(" ;")

                    if cleaned_style:
                        node["style"] = cleaned_style
                    else:
                        node.attrs.pop("style", None)

                node.attrs.pop("hidden", None)

        title = ""
        og_title = soup.select_one('meta[property="og:title"]')
        if og_title and og_title.get("content"):
            title = og_title.get("content", "").strip()
        if not title:
            activity_name = soup.select_one("#activity-name")
            if activity_name:
                title = activity_name.get_text(strip=True)

        author = ""
        og_author = soup.select_one('meta[property="og:article:author"]')
        if og_author and og_author.get("content"):
            author = og_author.get("content", "").strip()
        if not author:
            author_node = soup.select_one("#js_name")
            if author_node:
                author = author_node.get_text(strip=True)

        digest = ""
        og_desc = soup.select_one('meta[property="og:description"]')
        if og_desc and og_desc.get("content"):
            digest = og_desc.get("content", "").strip()

        cover_url = ""
        og_cover = soup.select_one('meta[property="twitter:image"]')
        if og_cover and og_cover.get("content"):
            cover_url = og_cover.get("content", "").strip()

        images: list[str] = []
        if content_node is not None:
            for img in content_node.find_all("img"):
                src = img.get("data-src") or img.get("src") or img.get("data-ori-src")
                if not src:
                    continue
                img["src"] = src
                if src not in images:
                    images.append(src)

        if not cover_url and images:
            cover_url = images[0]

        content_html = str(content_node) if content_node is not None else ""
        content_text = (
            content_node.get_text("\n", strip=True) if content_node is not None else ""
        )

        publish_ts = None
        ts_patterns = [
            r"var\s+ct\s*=\s*['\"](\d+)['\"]",
            r'"publish_time"\s*:\s*(\d+)',
            r"publish_time\s*=\s*['\"](\d+)['\"]",
        ]
        for pattern in ts_patterns:
            match = re.search(pattern, html or "")
            if match:
                try:
                    publish_ts = int(match.group(1))
                    break
                except ValueError:
                    continue

        return {
            "title": title,
            "author": author,
            "digest": digest,
            "cover_url": cover_url,
            "images": images,
            "content_html": content_html,
            "content_text": content_text,
            "publish_ts": publish_ts,
        }

    def list_articles(
        self,
        db: Session,
        mp_id: str | None = None,
        keyword: str | None = None,
        offset: int = 0,
        limit: int = 20,
    ) -> tuple[list[Article], int]:
        query = db.query(Article)

        if mp_id:
            query = query.filter(Article.mp_id == mp_id)
        if keyword:
            kw = f"%{keyword}%"
            query = query.filter(or_(Article.title.ilike(kw), Article.digest.ilike(kw)))

        total = query.count()
        rows = (
            query.order_by(desc(Article.publish_ts), desc(Article.updated_at))
            .offset(offset)
            .limit(limit)
            .all()
        )
        return rows, total

    def get_article(self, db: Session, article_id: str) -> Article | None:
        return db.query(Article).filter(Article.id == article_id).first()

    def refresh_article_content(self, db: Session, article: Article) -> Article:
        detail = self.fetch_article_detail(db, article.url)
        article.content_html = detail.get("content_html")
        article.content_text = detail.get("content_text")
        article.cover_url = detail.get("cover_url") or article.cover_url
        article.digest = detail.get("digest") or article.digest
        article.author = detail.get("author") or article.author
        if detail.get("publish_ts"):
            article.publish_ts = int(detail["publish_ts"])
        article.images_json = json.dumps(detail.get("images", []), ensure_ascii=False)
        article.updated_at = utcnow()
        db.add(article)
        db.commit()
        db.refresh(article)
        return article


article_service = ArticleService(wechat_client)
