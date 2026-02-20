#!/usr/bin/env python3
"""Capture frontend screenshots for README assets.

Usage example:
  ./.venv/bin/python scripts/capture_frontend_images.py --url http://127.0.0.1:5173
"""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path
from urllib.error import URLError
from urllib.request import urlopen

VIEW_ORDER = {
    "capture": 0,
    "mcp": 1,
    "database": 2,
}

DEFAULT_FILES = {
    "capture": "capture-view.png",
    "mcp": "mcp-view.png",
    "database": "database-view.png",
}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Capture README screenshots from the frontend UI."
    )
    parser.add_argument(
        "--url",
        default="http://127.0.0.1:5173",
        help="Frontend URL (default: %(default)s)",
    )
    parser.add_argument(
        "--out-dir",
        default="images",
        help="Output directory for screenshots (default: %(default)s)",
    )
    parser.add_argument(
        "--views",
        default="capture,mcp,database",
        help="Comma-separated views to capture: capture,mcp,database",
    )
    parser.add_argument(
        "--width",
        type=int,
        default=1728,
        help="Viewport width (default: %(default)s)",
    )
    parser.add_argument(
        "--height",
        type=int,
        default=1080,
        help="Viewport height (default: %(default)s)",
    )
    parser.add_argument(
        "--full-page",
        action="store_true",
        help="Capture full page instead of viewport",
    )
    parser.add_argument(
        "--timeout",
        type=int,
        default=45,
        help="Seconds to wait for frontend availability (default: %(default)s)",
    )
    parser.add_argument(
        "--settle-ms",
        type=int,
        default=900,
        help="Milliseconds to wait before each screenshot (default: %(default)s)",
    )
    return parser.parse_args()


def normalize_views(raw: str) -> list[str]:
    candidates = [item.strip().lower() for item in raw.split(",") if item.strip()]
    if not candidates:
        raise ValueError("No views specified")

    invalid = [item for item in candidates if item not in VIEW_ORDER]
    if invalid:
        supported = ", ".join(VIEW_ORDER.keys())
        raise ValueError(
            f"Unsupported views: {', '.join(invalid)} (supported: {supported})"
        )

    deduped: list[str] = []
    seen: set[str] = set()
    for item in candidates:
        if item not in seen:
            deduped.append(item)
            seen.add(item)
    return deduped


def wait_for_url_ready(url: str, timeout_seconds: int) -> None:
    deadline = time.time() + timeout_seconds
    last_error: Exception | None = None

    while time.time() < deadline:
        try:
            with urlopen(url, timeout=2):  # nosec B310 - local dev URL expected
                return
        except URLError as exc:
            last_error = exc
        except TimeoutError as exc:
            last_error = exc
        time.sleep(1)

    raise RuntimeError(
        f"Frontend URL is not reachable: {url}. "
        f"Start frontend first (for example: ./dev.sh up). "
        f"Last error: {last_error}"
    )


def activate_view(page, view: str) -> None:
    index = VIEW_ORDER[view]
    nav_buttons = page.locator(".sidebar-nav__btn")
    target = nav_buttons.nth(index)
    target.click()
    page.wait_for_function(
        """([selector, idx]) => {
          const all = document.querySelectorAll(selector);
          const node = all[idx];
          return Boolean(node && node.classList.contains('sidebar-nav__btn--active'));
        }""",
        arg=[".sidebar-nav__btn", index],
        timeout=10_000,
    )


def capture_views(
    url: str,
    out_dir: Path,
    views: list[str],
    width: int,
    height: int,
    full_page: bool,
    settle_ms: int,
) -> list[Path]:
    try:
        from playwright.sync_api import sync_playwright
    except ModuleNotFoundError as exc:
        raise RuntimeError(
            "playwright is not available in current Python environment. "
            "Use ./.venv/bin/python or install dependencies first."
        ) from exc

    out_dir.mkdir(parents=True, exist_ok=True)
    results: list[Path] = []

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        context = browser.new_context(viewport={"width": width, "height": height})
        page = context.new_page()

        page.goto(url, wait_until="domcontentloaded")
        page.wait_for_selector(".console-layout", timeout=20_000)

        for view in views:
            activate_view(page, view)
            page.wait_for_timeout(settle_ms)

            file_path = out_dir / DEFAULT_FILES[view]
            page.screenshot(path=str(file_path), full_page=full_page)
            results.append(file_path)

        context.close()
        browser.close()

    return results


def main() -> int:
    args = parse_args()

    try:
        views = normalize_views(args.views)
        if args.width <= 0 or args.height <= 0:
            raise ValueError("Viewport width/height must be positive")
        if args.timeout <= 0:
            raise ValueError("Timeout must be positive")
        if args.settle_ms < 0:
            raise ValueError("settle-ms must be >= 0")

        wait_for_url_ready(args.url, args.timeout)
        output_paths = capture_views(
            url=args.url,
            out_dir=Path(args.out_dir),
            views=views,
            width=args.width,
            height=args.height,
            full_page=args.full_page,
            settle_ms=args.settle_ms,
        )
    except (ValueError, RuntimeError) as exc:
        print(f"[capture-images] error: {exc}", file=sys.stderr)
        return 1

    print("[capture-images] done")
    for path in output_paths:
        print(f"- {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
