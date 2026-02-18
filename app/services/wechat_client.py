import json
import logging
import re
import time
import uuid
from pathlib import Path
from threading import Lock
from typing import Any

import requests
from sqlalchemy.orm import Session

from app.core.config import settings
from app.models import AuthSession


class WeChatAuthError(Exception):
    pass


logger = logging.getLogger(__name__)


class WeChatClient:
    """Minimal WeChat Official Account backend client."""

    def __init__(self) -> None:
        self.base_url = "https://mp.weixin.qq.com"
        self.home_url = f"{self.base_url}/cgi-bin/home"

        self._lock = Lock()
        self._session: requests.Session | None = None
        self._uuid: str | None = None
        self._fingerprint: str | None = None
        self._token: str | None = None

    def _new_session(self) -> requests.Session:
        session = requests.Session()
        session.headers.update(
            {
                "User-Agent": settings.user_agent,
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8",
                "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
                "Referer": f"{self.base_url}/",
                "Connection": "keep-alive",
            }
        )
        return session

    @staticmethod
    def _generate_uuid() -> str:
        return str(uuid.uuid4()).replace("-", "")

    @staticmethod
    def _extract_token(text: str) -> str | None:
        if not text:
            return None
        patterns = [
            r"token=([0-9A-Za-z_-]{5,})",
            r"\"token\"\s*:\s*\"([0-9A-Za-z_-]{5,})\"",
            r"token\s*[:=]\s*'?([0-9A-Za-z_-]{5,})'?",
        ]
        for pattern in patterns:
            match = re.search(pattern, text)
            if match:
                return match.group(1)
        return None

    def _extract_token_from_payload(self, payload: dict[str, Any]) -> str | None:
        if not payload:
            return None

        base_resp = payload.get("base_resp")
        if not isinstance(base_resp, dict):
            base_resp = {}

        candidates = [
            payload.get("redirect_url"),
            payload.get("redirectUrl"),
            payload.get("token"),
            base_resp.get("redirect_url"),
            base_resp.get("redirectUrl"),
            base_resp.get("token"),
        ]

        for item in candidates:
            if item is None:
                continue
            token = self._extract_token(str(item))
            if token:
                return token

        return None

    @staticmethod
    def _dedupe_keep_order(items: list[str | None]) -> list[str]:
        result: list[str] = []
        seen: set[str] = set()
        for item in items:
            if not item:
                continue
            token = str(item).strip()
            if not token or token in seen:
                continue
            seen.add(token)
            result.append(token)
        return result

    @staticmethod
    def _serialize_cookies(
        cookie_jar: requests.cookies.RequestsCookieJar,
    ) -> list[dict[str, Any]]:
        cookies: list[dict[str, Any]] = []
        for cookie in cookie_jar:
            cookies.append(
                {
                    "name": cookie.name,
                    "value": cookie.value,
                    "domain": cookie.domain,
                    "path": cookie.path,
                    "expires": cookie.expires,
                    "secure": cookie.secure,
                }
            )
        return cookies

    @staticmethod
    def _restore_cookies(
        session: requests.Session,
        cookies_data: list[dict[str, Any]],
    ) -> None:
        for cookie in cookies_data:
            session.cookies.set(
                cookie.get("name", ""),
                cookie.get("value", ""),
                domain=cookie.get("domain"),
                path=cookie.get("path", "/"),
            )

    def _get_auth_row(self, db: Session) -> AuthSession:
        row = db.get(AuthSession, 1)
        if row:
            return row
        row = AuthSession(id=1)
        db.add(row)
        db.commit()
        db.refresh(row)
        return row

    def _save_auth(self, db: Session, **fields: Any) -> AuthSession:
        row = self._get_auth_row(db)
        for key, value in fields.items():
            setattr(row, key, value)
        db.add(row)
        db.commit()
        db.refresh(row)
        return row

    def _load_runtime(self, db: Session) -> AuthSession:
        row = self._get_auth_row(db)
        self._session = self._new_session()

        self._uuid = row.uuid
        self._fingerprint = row.fingerprint
        self._token = row.token

        if row.cookie_json:
            try:
                cookies_data = json.loads(row.cookie_json)
                if isinstance(cookies_data, list):
                    self._restore_cookies(self._session, cookies_data)
            except json.JSONDecodeError:
                pass

        return row

    def get_auth_state(self, db: Session) -> dict[str, Any]:
        row = self._get_auth_row(db)

        # 兼容历史异常状态：若token依然有效，则自动恢复为logged_in
        if row.status != "logged_in" and row.token and row.cookie_json:
            try:
                self._load_runtime(db)
                if self._is_token_valid(row.token):
                    row = self._save_auth(db, status="logged_in", last_error=None)
            except Exception:
                pass

        return {
            "status": row.status,
            "token": row.token,
            "account_name": row.account_name,
            "account_avatar": row.account_avatar,
            "last_error": row.last_error,
            "updated_at": row.updated_at.isoformat() if row.updated_at else None,
        }

    def request_qr_code(self, db: Session) -> dict[str, Any]:
        with self._lock:
            self._session = self._new_session()
            self._uuid = None
            self._fingerprint = self._generate_uuid()
            self._token = None

            try:
                response = self._session.get(
                    f"{self.base_url}/",
                    timeout=settings.request_timeout,
                    verify=settings.verify_ssl,
                )
                response.raise_for_status()

                qr_info = self._extract_qr_info(response.text)
                if not qr_info:
                    qr_info = self._fallback_qr_info()

                if not qr_info:
                    raise WeChatAuthError("无法初始化登录流程，未拿到有效二维码参数")

                self._uuid = qr_info["uuid"]
                qr_url = qr_info["qr_url"]

                qr_resp = self._session.get(
                    qr_url,
                    timeout=settings.request_timeout,
                    verify=settings.verify_ssl,
                )
                qr_resp.raise_for_status()

                content_type = (qr_resp.headers.get("Content-Type") or "").lower()
                if "image" not in content_type or not qr_resp.content:
                    raise WeChatAuthError("微信未返回二维码图片，请稍后重试")

                qr_path = Path(settings.qr_file)
                qr_path.parent.mkdir(parents=True, exist_ok=True)
                qr_path.write_bytes(qr_resp.content)
            except WeChatAuthError as exc:
                logger.warning("request_qr_code failed: %s", exc)
                self._save_auth(
                    db,
                    status="error",
                    last_error=str(exc),
                    uuid=None,
                    token=None,
                    fingerprint=None,
                    cookie_json=None,
                )
                raise
            except Exception as exc:  # noqa: BLE001
                logger.exception("request_qr_code unexpected error")
                self._save_auth(
                    db,
                    status="error",
                    last_error=f"获取二维码异常: {exc}",
                    uuid=None,
                    token=None,
                    fingerprint=None,
                    cookie_json=None,
                )
                raise WeChatAuthError("获取二维码失败，请稍后重试") from exc

            cookies_json = json.dumps(
                self._serialize_cookies(self._session.cookies), ensure_ascii=False
            )
            self._save_auth(
                db,
                status="waiting_scan",
                uuid=self._uuid,
                token=None,
                fingerprint=self._fingerprint,
                cookie_json=cookies_json,
                account_name=None,
                account_avatar=None,
                last_error=None,
            )

            return {
                "uuid": self._uuid,
                "qr_file": str(qr_path.resolve()),
                "qr_image_url": f"{settings.api_prefix}/auth/qr/image?t={int(time.time())}",
            }

    def _extract_qr_info(self, html_text: str) -> dict[str, str] | None:
        if not html_text:
            return None

        qr_match = re.search(
            r"(https://mp\.weixin\.qq\.com/cgi-bin/loginqrcode\?action=getqrcode&param=\d+)",
            html_text,
        )
        uuid_match = re.search(
            r"[\"']uuid[\"']\s*[:=]\s*[\"']([^\"']+)[\"']", html_text
        )

        if qr_match and uuid_match:
            return {"qr_url": qr_match.group(1), "uuid": uuid_match.group(1)}
        return None

    def _fallback_qr_info(self) -> dict[str, str] | None:
        if not self._session:
            return None

        fallback_uuid = self._start_login_for_qr()
        if not fallback_uuid:
            return None

        ts = int(time.time() * 1000)
        qr_url = (
            f"{self.base_url}/cgi-bin/scanloginqrcode"
            f"?action=getqrcode&uuid={fallback_uuid}&random={ts}"
        )

        try:
            resp = self._session.get(
                qr_url, timeout=settings.request_timeout, verify=settings.verify_ssl
            )
            if resp.ok and "image" in (resp.headers.get("Content-Type") or ""):
                return {"uuid": fallback_uuid, "qr_url": qr_url}
            logger.warning(
                "fallback qr request returned non-image: status=%s content-type=%s len=%s",
                resp.status_code,
                resp.headers.get("Content-Type"),
                len(resp.content or b""),
            )
        except requests.RequestException:
            return None
        return None

    def _start_login_for_qr(self) -> str | None:
        if not self._session:
            return None

        token = self._session.cookies.get("token", "")
        fingerprint = self._fingerprint or self._generate_uuid()
        self._fingerprint = fingerprint

        payload = {
            "fingerprint": fingerprint,
            "token": token,
            "lang": "zh_CN",
            "f": "json",
            "ajax": "1",
            "redirect_url": (
                "/cgi-bin/settingpage?t=setting/index"
                f"&amp;action=index&amp;token={token}&amp;lang=zh_CN"
            ),
            "login_type": "3",
        }

        try:
            response = self._session.post(
                f"{self.base_url}/cgi-bin/bizlogin?action=startlogin",
                data=payload,
                timeout=settings.request_timeout,
                verify=settings.verify_ssl,
            )
            response.raise_for_status()

            body: dict[str, Any] = {}
            try:
                body = response.json()
            except ValueError:
                body = {}

            ret = body.get("base_resp", {}).get("ret")
            if ret not in (None, 0):
                logger.warning("startlogin ret=%s body=%s", ret, body)
                return None

            uuid_value = (
                response.cookies.get("uuid")
                or self._session.cookies.get("uuid")
                or body.get("uuid")
            )
            return uuid_value
        except requests.RequestException as exc:
            logger.warning("startlogin request failed: %s", exc)
            return None

    def _resolve_token_from_loginpage(self) -> str | None:
        if not self._session:
            return None

        try:
            response = self._session.get(
                f"{self.base_url}/cgi-bin/loginpage",
                params={"url": "/cgi-bin/home"},
                timeout=settings.request_timeout,
                verify=settings.verify_ssl,
                allow_redirects=True,
            )
            response.raise_for_status()

            token = self._extract_token(response.url)
            if token:
                return token

            token = self._extract_token(response.text)
            if token:
                return token

            for history in response.history:
                token = self._extract_token(history.headers.get("Location", ""))
                if token:
                    return token
        except requests.RequestException as exc:
            logger.warning("resolve token from loginpage failed: %s", exc)
            return None

        return None

    def _is_token_valid(self, token: str) -> bool:
        if not self._session or not token:
            return False

        try:
            response = self._session.get(
                f"{self.base_url}/cgi-bin/switchacct",
                params={
                    "action": "get_acct_list",
                    "fingerprint": self._fingerprint or self._generate_uuid(),
                    "token": token,
                    "lang": "zh_CN",
                    "f": "json",
                    "ajax": 1,
                },
                headers={
                    "X-Requested-With": "XMLHttpRequest",
                    "Referer": f"{self.base_url}/cgi-bin/home?t=home/index&lang=zh_CN&token={token}",
                },
                timeout=settings.request_timeout,
                verify=settings.verify_ssl,
            )
            response.raise_for_status()
            payload = response.json()
            return payload.get("base_resp", {}).get("ret") == 0
        except Exception:
            return False

    def poll_login_status(self, db: Session) -> dict[str, Any]:
        row = self._load_runtime(db)
        if not self._session:
            raise WeChatAuthError("登录会话未初始化")

        if row.status == "logged_in" and row.token:
            if self._is_token_valid(row.token):
                return {
                    "status": "logged_in",
                    "token": row.token,
                    "account_name": row.account_name,
                    "account_avatar": row.account_avatar,
                }
            self._save_auth(
                db,
                status="error",
                token=None,
                account_name=None,
                account_avatar=None,
                last_error="登录状态失效，请重新扫码",
            )
            return {"status": "error", "error": "登录状态失效，请重新扫码"}

        if not row.uuid:
            return {"status": "logged_out"}

        ask_url = f"{self.base_url}/cgi-bin/scanloginqrcode"
        params = {
            "action": "ask",
            "fingerprint": row.fingerprint or self._generate_uuid(),
            "lang": "zh_CN",
            "f": "json",
            "ajax": 1,
        }

        try:
            response = self._session.get(
                ask_url,
                params=params,
                timeout=settings.request_timeout,
                verify=settings.verify_ssl,
            )
            response.raise_for_status()
            data = response.json()
        except Exception as exc:  # noqa: BLE001
            self._save_auth(db, status="error", last_error=f"轮询扫码状态失败: {exc}")
            return {"status": "error", "error": str(exc)}

        status_code = data.get("status")

        if status_code in (1, 3):
            return self._finalize_login(db)
        if status_code in (2, 4):
            self._save_auth(db, status="scanned", last_error=None)
            return {"status": "scanned"}
        if status_code in (5, 6):
            self._save_auth(db, status="expired", last_error="二维码已过期")
            return {"status": "expired"}

        self._save_auth(db, status="waiting_scan", last_error=None)
        return {"status": "waiting_scan"}

    def _finalize_login(self, db: Session) -> dict[str, Any]:
        if not self._session:
            raise WeChatAuthError("登录会话未初始化")

        prev_auth = self._get_auth_row(db)

        login_resp = self._session.post(
            f"{self.base_url}/cgi-bin/bizlogin?action=login",
            data={
                "userlang": "zh_CN",
                "redirect_url": "",
                "cookie_forbidden": "0",
                "cookie_cleaned": "0",
                "plugin_used": "0",
                "login_type": "3",
                "fingerprint": self._fingerprint or self._generate_uuid(),
                "token": "",
                "lang": "zh_CN",
                "f": "json",
                "ajax": "1",
            },
            timeout=settings.request_timeout,
            verify=settings.verify_ssl,
        )
        login_resp.raise_for_status()

        login_payload: dict[str, Any]
        try:
            login_payload = login_resp.json()
        except ValueError:
            login_payload = {}

        candidate_tokens = self._dedupe_keep_order(
            [
                self._extract_token_from_payload(login_payload),
                self._extract_token(login_resp.url),
                self._extract_token(login_resp.text),
                login_resp.cookies.get("token"),
                self._session.cookies.get("token"),
                self._resolve_token_from_loginpage(),
                prev_auth.token,
            ]
        )

        token = None
        for candidate in candidate_tokens:
            if self._is_token_valid(candidate):
                token = candidate
                break

        if token is None and candidate_tokens:
            token = candidate_tokens[0]

        if not token:
            self._save_auth(
                db,
                status="error",
                token=None,
                account_name=None,
                account_avatar=None,
                last_error="扫码成功但未获取到 token",
            )
            return {"status": "error", "error": "未获取到 token"}

        self._token = token
        account = self._fetch_account_info(token)

        cookies_json = json.dumps(
            self._serialize_cookies(self._session.cookies), ensure_ascii=False
        )
        self._save_auth(
            db,
            status="logged_in",
            token=token,
            cookie_json=cookies_json,
            account_name=account.get("name"),
            account_avatar=account.get("avatar"),
            last_error=None,
        )

        return {
            "status": "logged_in",
            "token": token,
            "account_name": account.get("name"),
            "account_avatar": account.get("avatar"),
        }

    def _fetch_account_info(self, token: str) -> dict[str, Any]:
        if not self._session:
            return {}

        try:
            response = self._session.get(
                f"{self.base_url}/cgi-bin/switchacct",
                params={
                    "action": "get_acct_list",
                    "fingerprint": self._fingerprint or self._generate_uuid(),
                    "token": token,
                    "lang": "zh_CN",
                    "f": "json",
                    "ajax": 1,
                },
                headers={
                    "X-Requested-With": "XMLHttpRequest",
                    "Referer": f"{self.base_url}/cgi-bin/home?t=home/index&lang=zh_CN&token={token}",
                },
                timeout=settings.request_timeout,
                verify=settings.verify_ssl,
            )
            response.raise_for_status()
            payload = response.json()
            biz_list = payload.get("biz_list", {}).get("list", [])
            if not biz_list:
                return {}

            first = biz_list[0]
            return {
                "name": first.get("nickname") or first.get("username"),
                "avatar": first.get("headimgurl"),
            }
        except Exception:
            return {}

    def logout(self, db: Session) -> None:
        with self._lock:
            self._session = None
            self._uuid = None
            self._fingerprint = None
            self._token = None

            self._save_auth(
                db,
                status="logged_out",
                uuid=None,
                token=None,
                fingerprint=None,
                cookie_json=None,
                account_name=None,
                account_avatar=None,
                last_error=None,
            )

            qr_path = Path(settings.qr_file)
            if qr_path.exists():
                qr_path.unlink(missing_ok=True)

    def ensure_login(self, db: Session) -> tuple[requests.Session, str]:
        row = self._load_runtime(db)
        if row.status != "logged_in" or not row.token:
            raise WeChatAuthError("未登录，请先扫码认证")
        if not self._session:
            raise WeChatAuthError("会话初始化失败")
        self._token = row.token
        return self._session, row.token

    def search_mps(
        self, db: Session, keyword: str, offset: int, limit: int
    ) -> dict[str, Any]:
        session, token = self.ensure_login(db)

        response = session.get(
            f"{self.base_url}/cgi-bin/searchbiz",
            params={
                "action": "search_biz",
                "begin": offset,
                "count": limit,
                "query": keyword,
                "token": token,
                "lang": "zh_CN",
                "f": "json",
                "ajax": 1,
            },
            timeout=settings.request_timeout,
            verify=settings.verify_ssl,
        )
        response.raise_for_status()
        payload = response.json()

        base_resp = payload.get("base_resp", {})
        if base_resp.get("ret") != 0:
            raise WeChatAuthError(
                f"搜索公众号失败: {base_resp.get('err_msg', 'unknown error')}"
            )

        mps: list[dict[str, Any]] = []
        for item in payload.get("list", []):
            mps.append(
                {
                    "fakeid": item.get("fakeid", ""),
                    "nickname": item.get("nickname") or item.get("nick_name") or "",
                    "alias": item.get("alias"),
                    "avatar": item.get("round_head_img") or item.get("head_img"),
                    "intro": item.get("signature"),
                    "biz": item.get("biz"),
                }
            )

        return {"total": payload.get("total", len(mps)), "list": mps}

    def fetch_publish_page(
        self, db: Session, fakeid: str, begin: int, count: int = 5
    ) -> dict[str, Any]:
        session, token = self.ensure_login(db)
        response = session.get(
            f"{self.base_url}/cgi-bin/appmsgpublish",
            params={
                "sub": "list",
                "sub_action": "list_ex",
                "begin": begin,
                "count": count,
                "fakeid": fakeid,
                "token": token,
                "lang": "zh_CN",
                "f": "json",
                "ajax": 1,
            },
            timeout=settings.request_timeout,
            verify=settings.verify_ssl,
        )
        response.raise_for_status()
        payload = response.json()

        base_resp = payload.get("base_resp", {})
        ret = base_resp.get("ret")
        if ret != 0:
            if ret == 200003:
                raise WeChatAuthError("登录状态失效，请重新扫码")
            if ret == 200013:
                raise WeChatAuthError("请求过于频繁，被微信限流")
            raise WeChatAuthError(
                f"拉取文章失败: {base_resp.get('err_msg', 'unknown error')}"
            )

        return payload

    def fetch_appmsg_page(
        self, db: Session, fakeid: str, begin: int, count: int = 5
    ) -> dict[str, Any]:
        session, token = self.ensure_login(db)
        response = session.get(
            f"{self.base_url}/cgi-bin/appmsg",
            params={
                "action": "list_ex",
                "begin": begin,
                "count": count,
                "fakeid": fakeid,
                "type": 9,
                "token": token,
                "lang": "zh_CN",
                "f": "json",
                "ajax": 1,
            },
            timeout=settings.request_timeout,
            verify=settings.verify_ssl,
        )
        response.raise_for_status()
        payload = response.json()

        base_resp = payload.get("base_resp", {})
        ret = base_resp.get("ret")
        if ret != 0:
            if ret == 200003:
                raise WeChatAuthError("登录状态失效，请重新扫码")
            if ret == 200013:
                raise WeChatAuthError("请求过于频繁，被微信限流")
            raise WeChatAuthError(
                f"拉取文章失败: {base_resp.get('err_msg', 'unknown error')}"
            )

        return payload

    def fetch_article_html(self, db: Session, article_url: str) -> str:
        session, _ = self.ensure_login(db)

        response = session.get(
            article_url,
            headers={
                "Referer": f"{self.base_url}/",
                "User-Agent": settings.user_agent,
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            },
            timeout=settings.request_timeout,
            verify=settings.verify_ssl,
        )
        response.raise_for_status()
        return response.text


wechat_client = WeChatClient()
