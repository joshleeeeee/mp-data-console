import html
import re
import zipfile
from datetime import datetime
from pathlib import Path

from bs4 import BeautifulSoup
from markdownify import markdownify as to_markdown
from sqlalchemy.orm import Session

from app.core.config import settings
from app.models import Article
from app.services.image_service import ImageProxyError, image_proxy_service


class ExportError(Exception):
    pass


class ExportService:
    def __init__(self) -> None:
        self.export_root = Path(settings.export_dir)
        self.export_root.mkdir(parents=True, exist_ok=True)

    @staticmethod
    def _safe_filename(name: str) -> str:
        cleaned = re.sub(r"[^A-Za-z0-9._-]+", "_", name.strip())
        cleaned = cleaned.strip("._")
        return cleaned or "article"

    @staticmethod
    def _publish_time(article: Article) -> str:
        if article.publish_ts:
            try:
                return datetime.fromtimestamp(article.publish_ts).strftime(
                    "%Y%m%d_%H%M%S"
                )
            except Exception:
                pass
        return article.created_at.strftime("%Y%m%d_%H%M%S")

    def _article_base_name(self, article: Article) -> str:
        title_part = self._safe_filename(article.title)[:60]
        ts_part = self._publish_time(article)
        return f"{ts_part}_{title_part}_{article.id[-8:]}"

    @staticmethod
    def _extract_fragment_html(soup: BeautifulSoup) -> str:
        if soup.body is not None:
            return "".join(str(node) for node in soup.body.contents)
        return str(soup)

    def _rewrite_images_to_proxy(self, content_html: str) -> str:
        if not content_html:
            return ""

        soup = BeautifulSoup(content_html, "lxml")
        for img in soup.find_all("img"):
            src = img.get("src") or img.get("data-src") or img.get("data-ori-src")
            if not src:
                continue
            if src.startswith(("data:", "blob:")):
                continue
            if src.startswith(f"{settings.api_prefix}/assets/image"):
                continue

            try:
                img["src"] = image_proxy_service.build_proxy_path(src)
            except ImageProxyError:
                continue

        return self._extract_fragment_html(soup)

    def _localize_images(
        self,
        content_html: str,
        out_dir: Path,
        base_name: str,
    ) -> tuple[str, Path | None]:
        if not content_html:
            return "", None

        soup = BeautifulSoup(content_html, "lxml")
        assets_dir = out_dir / f"{base_name}_assets"
        localized_count = 0

        for img in soup.find_all("img"):
            src = img.get("src") or img.get("data-src") or img.get("data-ori-src")
            if not src:
                continue
            if src.startswith(("data:", "blob:")):
                continue

            try:
                local_file = image_proxy_service.download_to_file(src, assets_dir)
                img["src"] = f"{assets_dir.name}/{local_file.name}"
                localized_count += 1
            except ImageProxyError:
                try:
                    img["src"] = image_proxy_service.build_proxy_path(src)
                except ImageProxyError:
                    continue

        if localized_count <= 0:
            if assets_dir.exists():
                for file_path in assets_dir.glob("*"):
                    file_path.unlink(missing_ok=True)
                assets_dir.rmdir()
            return self._extract_fragment_html(soup), None

        return self._extract_fragment_html(soup), assets_dir

    @staticmethod
    def _build_html_document(article: Article, content_html: str) -> str:
        title = html.escape(article.title or "Untitled")
        return f"""<!DOCTYPE html>
<html lang=\"zh-CN\">
<head>
  <meta charset=\"utf-8\" />
  <meta name=\"viewport\" content=\"width=device-width, initial-scale=1\" />
  <title>{title}</title>
  <style>
    body {{
      font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, 'PingFang SC', 'Hiragino Sans GB', sans-serif;
      margin: 24px auto;
      max-width: 900px;
      color: #1f2937;
      line-height: 1.8;
      padding: 0 16px;
    }}
    h1 {{ line-height: 1.4; margin-bottom: 12px; }}
    .meta {{ color: #6b7280; font-size: 14px; margin-bottom: 18px; }}
    img {{ max-width: 100%; height: auto; display: block; margin: 12px auto; }}
    pre {{ white-space: pre-wrap; word-break: break-word; }}
    blockquote {{ border-left: 3px solid #d1d5db; margin: 12px 0; padding: 0 12px; color: #4b5563; }}
    a {{ color: #0f766e; text-decoration: none; }}
  </style>
</head>
<body>
  <h1>{title}</h1>
  <div class=\"meta\">来源链接: {html.escape(article.url)}</div>
  <article>{content_html}</article>
</body>
</html>"""

    def export_article(self, article: Article, export_format: str) -> dict[str, str]:
        export_format = export_format.lower().strip()
        if export_format not in {"markdown", "html", "pdf"}:
            raise ExportError("仅支持 markdown/html/pdf 导出")

        date_dir = datetime.now().strftime("%Y%m%d")
        out_dir = self.export_root / date_dir
        out_dir.mkdir(parents=True, exist_ok=True)

        base_name = self._article_base_name(article)
        content_html = article.content_html or ""
        assets_dir: Path | None = None

        if export_format == "markdown":
            out_path = out_dir / f"{base_name}.md"
            markdown_source = self._rewrite_images_to_proxy(content_html)
            markdown = to_markdown(markdown_source, heading_style="ATX")
            out_path.write_text(
                f"# {article.title}\n\n"
                f"- 原文: {article.url}\n"
                f"- 作者: {article.author or ''}\n\n"
                f"{markdown}\n",
                encoding="utf-8",
            )
        elif export_format == "html":
            out_path = out_dir / f"{base_name}.html"
            localized_html, assets_dir = self._localize_images(
                content_html, out_dir, base_name
            )
            html_doc = self._build_html_document(article, localized_html)
            out_path.write_text(html_doc, encoding="utf-8")
        else:
            out_path = out_dir / f"{base_name}.pdf"
            localized_html, _ = self._localize_images(content_html, out_dir, base_name)
            html_doc = self._build_html_document(article, localized_html)

            tmp_html = out_dir / f"{base_name}.pdf_source.html"
            tmp_html.write_text(html_doc, encoding="utf-8")
            try:
                self._export_pdf_from_html_file(tmp_html, out_path)
            finally:
                tmp_html.unlink(missing_ok=True)

        result = {
            "format": export_format,
            "file_path": str(out_path.resolve()),
            "file_name": out_path.name,
            "download_url": f"{settings.api_prefix}/exports/files/{date_dir}/{out_path.name}",
        }
        if assets_dir:
            result["assets_dir"] = str(assets_dir.resolve())
        return result

    def _export_pdf_from_html_file(self, html_file: Path, output_path: Path) -> None:
        try:
            from playwright.sync_api import sync_playwright
        except Exception as exc:  # noqa: BLE001
            raise ExportError("Playwright 未安装，无法导出 PDF") from exc

        browser_name = settings.playwright_browser.strip().lower()
        try:
            with sync_playwright() as pw:
                browser_factory = getattr(pw, browser_name, pw.chromium)
                browser = browser_factory.launch(headless=settings.playwright_headless)
                page = browser.new_page()
                page.goto(html_file.resolve().as_uri(), wait_until="networkidle")
                page.pdf(
                    path=str(output_path),
                    format="A4",
                    print_background=True,
                    margin={
                        "top": "15mm",
                        "right": "10mm",
                        "bottom": "15mm",
                        "left": "10mm",
                    },
                )
                browser.close()
        except Exception as exc:  # noqa: BLE001
            raise ExportError(f"PDF 导出失败: {exc}") from exc

    def export_batch(
        self, db: Session, article_ids: list[str], export_format: str
    ) -> dict[str, str | int]:
        if not article_ids:
            raise ExportError("article_ids 不能为空")

        articles = db.query(Article).filter(Article.id.in_(article_ids)).all()
        if not articles:
            raise ExportError("未找到可导出的文章")

        exported_files = []
        exported_asset_dirs = []
        for article in articles:
            result = self.export_article(article, export_format)
            exported_files.append(Path(result["file_path"]))
            assets_dir = result.get("assets_dir")
            if assets_dir:
                exported_asset_dirs.append(Path(assets_dir))

        now = datetime.now().strftime("%Y%m%d_%H%M%S")
        zip_name = f"batch_{export_format}_{now}.zip"
        date_dir = datetime.now().strftime("%Y%m%d")
        zip_path = self.export_root / date_dir / zip_name

        with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_DEFLATED) as zf:
            for file_path in exported_files:
                zf.write(file_path, arcname=file_path.name)

            for assets_dir in exported_asset_dirs:
                if not assets_dir.exists() or not assets_dir.is_dir():
                    continue
                for asset_file in assets_dir.rglob("*"):
                    if not asset_file.is_file():
                        continue
                    zf.write(
                        asset_file,
                        arcname=f"{assets_dir.name}/{asset_file.name}",
                    )

        return {
            "count": len(exported_files),
            "zip_file": str(zip_path.resolve()),
            "download_url": f"{settings.api_prefix}/exports/files/{date_dir}/{zip_name}",
        }

    def resolve_file(self, relative_path: str) -> Path:
        target = (self.export_root / relative_path).resolve()
        root = self.export_root.resolve()
        if root not in target.parents and target != root:
            raise ExportError("非法文件路径")
        if not target.exists() or not target.is_file():
            raise ExportError("文件不存在")
        return target


export_service = ExportService()
