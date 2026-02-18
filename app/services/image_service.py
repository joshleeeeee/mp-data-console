import hashlib
import json
import mimetypes
import time
from pathlib import Path
from urllib.parse import quote, unquote, urlparse

import requests

from app.core.config import settings


class ImageProxyError(Exception):
    pass


class ImageProxyService:
    def __init__(self) -> None:
        self.cache_root = Path(settings.data_dir) / "image_cache"
        self.cache_root.mkdir(parents=True, exist_ok=True)

        self.cache_ttl_seconds = 7 * 24 * 3600
        self.retry_times = 3
        self.retry_backoff_seconds = 0.35

        self.allowed_exact_hosts = {
            "mmbiz.qpic.cn",
            "mmecoa.qpic.cn",
            "mmbiz.qlogo.cn",
            "wx.qlogo.cn",
            "thirdwx.qlogo.cn",
            "res.wx.qq.com",
            "mp.weixin.qq.com",
        }
        self.allowed_suffix_hosts = {
            ".qpic.cn",
            ".qlogo.cn",
            ".weixin.qq.com",
        }

    def _is_allowed_host(self, host: str) -> bool:
        host = host.lower().split(":")[0]
        if host in self.allowed_exact_hosts:
            return True
        for suffix in self.allowed_suffix_hosts:
            if host.endswith(suffix):
                return True
        return False

    def normalize_image_url(self, raw_url: str) -> str:
        if not raw_url:
            raise ImageProxyError("图片地址不能为空")

        url = unquote(raw_url.strip())
        if url.startswith("//"):
            url = f"https:{url}"
        elif not url.startswith(("http://", "https://")):
            url = f"https://{url}"

        parsed = urlparse(url)
        if parsed.scheme not in {"http", "https"}:
            raise ImageProxyError("仅支持 http/https 图片链接")
        if not parsed.netloc:
            raise ImageProxyError("无效图片链接")
        if not self._is_allowed_host(parsed.netloc):
            raise ImageProxyError("当前仅允许代理微信图片域名")

        return url

    @staticmethod
    def _sniff_content_type(data: bytes) -> str | None:
        if not data:
            return None
        if data.startswith(b"\xff\xd8\xff"):
            return "image/jpeg"
        if data.startswith(b"\x89PNG\r\n\x1a\n"):
            return "image/png"
        if data.startswith(b"GIF87a") or data.startswith(b"GIF89a"):
            return "image/gif"
        if data.startswith(b"RIFF") and data[8:12] == b"WEBP":
            return "image/webp"
        return None

    @staticmethod
    def _content_type_ext(content_type: str) -> str:
        ext = mimetypes.guess_extension(content_type or "") or ""
        if ext == ".jpe":
            return ".jpg"
        return ext

    def build_proxy_path(self, raw_url: str) -> str:
        normalized = self.normalize_image_url(raw_url)
        encoded = quote(normalized, safe="")
        return f"{settings.api_prefix}/assets/image?url={encoded}"

    def _cache_paths(self, normalized_url: str) -> tuple[Path, Path]:
        digest = hashlib.sha256(normalized_url.encode("utf-8")).hexdigest()
        meta_path = self.cache_root / f"{digest}.json"
        bin_path = self.cache_root / f"{digest}.bin"
        return meta_path, bin_path

    def _read_cache(self, normalized_url: str) -> tuple[bytes, str] | None:
        meta_path, bin_path = self._cache_paths(normalized_url)
        if not meta_path.exists() or not bin_path.exists():
            return None

        try:
            meta = json.loads(meta_path.read_text(encoding="utf-8"))
        except Exception:
            return None

        updated_at = int(meta.get("updated_at", 0))
        if int(time.time()) - updated_at > self.cache_ttl_seconds:
            return None

        content_type = (meta.get("content_type") or "").strip()
        if not content_type:
            return None

        try:
            return bin_path.read_bytes(), content_type
        except Exception:
            return None

    def _write_cache(self, normalized_url: str, data: bytes, content_type: str) -> None:
        meta_path, bin_path = self._cache_paths(normalized_url)
        meta = {
            "url": normalized_url,
            "content_type": content_type,
            "size": len(data),
            "updated_at": int(time.time()),
        }
        bin_path.write_bytes(data)
        meta_path.write_text(json.dumps(meta, ensure_ascii=False), encoding="utf-8")

    def fetch_image(self, raw_url: str, force: bool = False) -> tuple[bytes, str, bool]:
        normalized = self.normalize_image_url(raw_url)

        if not force:
            cached = self._read_cache(normalized)
            if cached:
                data, content_type = cached
                return data, content_type, True

        request_headers = {
            "User-Agent": settings.user_agent,
            "Referer": "https://mp.weixin.qq.com/",
            "Accept": "image/avif,image/webp,image/apng,image/*,*/*;q=0.8",
            "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
            "Connection": "keep-alive",
        }

        last_error = ""
        for index in range(self.retry_times):
            try:
                response = requests.get(
                    normalized,
                    headers=request_headers,
                    timeout=settings.request_timeout,
                    verify=settings.verify_ssl,
                    allow_redirects=True,
                )

                if response.status_code >= 500 or response.status_code == 429:
                    last_error = f"微信图片服务暂不可用（{response.status_code}）"
                    time.sleep(self.retry_backoff_seconds * (index + 1))
                    continue

                if response.status_code >= 400:
                    raise ImageProxyError(f"图片请求失败（{response.status_code}）")

                data = response.content or b""
                if not data:
                    raise ImageProxyError("图片响应为空")

                header_content_type = (
                    (response.headers.get("Content-Type") or "")
                    .split(";")[0]
                    .strip()
                    .lower()
                )
                content_type = header_content_type
                if not content_type.startswith("image/"):
                    content_type = self._sniff_content_type(data) or ""

                if not content_type:
                    guessed = mimetypes.guess_type(urlparse(normalized).path)[0] or ""
                    if guessed.startswith("image/"):
                        content_type = guessed

                if not content_type.startswith("image/"):
                    raise ImageProxyError("目标地址未返回图片内容")

                self._write_cache(normalized, data, content_type)
                return data, content_type, False
            except ImageProxyError:
                raise
            except requests.RequestException as exc:
                last_error = str(exc)
                time.sleep(self.retry_backoff_seconds * (index + 1))

        if last_error:
            raise ImageProxyError(f"图片代理失败：{last_error}")
        raise ImageProxyError("图片代理失败")

    def download_to_file(self, raw_url: str, target_dir: Path) -> Path:
        normalized = self.normalize_image_url(raw_url)
        data, content_type, _ = self.fetch_image(normalized)

        digest = hashlib.sha256(normalized.encode("utf-8")).hexdigest()[:24]
        ext = self._content_type_ext(content_type)
        if not ext:
            ext = Path(urlparse(normalized).path).suffix or ".img"

        target_dir.mkdir(parents=True, exist_ok=True)
        file_path = target_dir / f"{digest}{ext}"
        if not file_path.exists():
            file_path.write_bytes(data)
        return file_path


image_proxy_service = ImageProxyService()
