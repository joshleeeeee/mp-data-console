#!/usr/bin/env python3

from __future__ import annotations

import argparse
import re
import subprocess
import sys
from pathlib import Path

START_MARKER = "<!-- README_IMAGES:START -->"
END_MARKER = "<!-- README_IMAGES:END -->"

VIEW_META = {
    "capture": {"file": "capture-view.png", "label": "抓取视图"},
    "mcp": {"file": "mcp-view.png", "label": "MCP 接入"},
    "database": {"file": "database-view.png", "label": "数据库"},
}

DEFAULT_VIEW_ORDER = ["capture", "mcp", "database"]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="One-click capture frontend screenshots and sync README image block."
    )
    parser.add_argument(
        "--url",
        default="http://127.0.0.1:5173",
        help="Frontend URL (default: %(default)s)",
    )
    parser.add_argument(
        "--readme",
        default="README.md",
        help="README file path, relative to repo root (default: %(default)s)",
    )
    parser.add_argument(
        "--image-dir",
        default="images",
        help="Directory used for screenshots (default: %(default)s)",
    )
    parser.add_argument(
        "--views",
        default=",".join(DEFAULT_VIEW_ORDER),
        help="Comma-separated views to capture: capture,mcp,database",
    )
    parser.add_argument("--width", type=int, default=1728)
    parser.add_argument("--height", type=int, default=1080)
    parser.add_argument("--timeout", type=int, default=45)
    parser.add_argument("--settle-ms", type=int, default=900)
    parser.add_argument("--full-page", action="store_true")
    parser.add_argument(
        "--skip-capture",
        action="store_true",
        help="Skip screenshot capture and only update README block",
    )
    return parser.parse_args()


def normalize_views(raw_views: str) -> list[str]:
    views = [item.strip().lower() for item in raw_views.split(",") if item.strip()]
    if not views:
        raise ValueError("No views specified")

    invalid = [item for item in views if item not in VIEW_META]
    if invalid:
        supported = ", ".join(DEFAULT_VIEW_ORDER)
        raise ValueError(
            f"Unsupported views: {', '.join(invalid)} (supported: {supported})"
        )

    deduped: list[str] = []
    seen: set[str] = set()
    for item in views:
        if item not in seen:
            deduped.append(item)
            seen.add(item)
    return deduped


def run_capture(repo_root: Path, args: argparse.Namespace, views: list[str]) -> None:
    capture_script = repo_root / "scripts" / "capture_frontend_images.py"
    if not capture_script.exists():
        raise RuntimeError(f"Missing capture script: {capture_script}")

    command = [
        sys.executable,
        str(capture_script),
        "--url",
        args.url,
        "--out-dir",
        args.image_dir,
        "--views",
        ",".join(views),
        "--width",
        str(args.width),
        "--height",
        str(args.height),
        "--timeout",
        str(args.timeout),
        "--settle-ms",
        str(args.settle_ms),
    ]
    if args.full_page:
        command.append("--full-page")

    result = subprocess.run(command, cwd=repo_root, check=False)
    if result.returncode != 0:
        raise RuntimeError("Screenshot capture failed")


def to_readme_path(repo_root: Path, image_dir: str, filename: str) -> str:
    base = Path(image_dir) / filename
    if base.is_absolute():
        try:
            return base.relative_to(repo_root).as_posix()
        except ValueError:
            return base.as_posix()
    return base.as_posix()


def build_image_block(repo_root: Path, image_dir: str, views: list[str]) -> str:
    headers = []
    links = []
    for view in views:
        label = VIEW_META[view]["label"]
        file_name = VIEW_META[view]["file"]
        rel_path = to_readme_path(repo_root, image_dir, file_name)
        headers.append(label)
        links.append(f"![{label}]({rel_path})")

    header_line = "| " + " | ".join(headers) + " |"
    sep_line = "| " + " | ".join(["---"] * len(headers)) + " |"
    link_line = "| " + " | ".join(links) + " |"

    return "\n".join(
        [
            START_MARKER,
            header_line,
            sep_line,
            link_line,
            END_MARKER,
        ]
    )


def update_readme(repo_root: Path, readme_path: Path, image_block: str) -> bool:
    if not readme_path.exists():
        raise RuntimeError(f"README file not found: {readme_path}")

    original = readme_path.read_text(encoding="utf-8")
    pattern = re.compile(re.escape(START_MARKER) + r"[\s\S]*?" + re.escape(END_MARKER))

    if pattern.search(original):
        updated = pattern.sub(image_block, original, count=1)
    else:
        section = "## 界面预览\n\n" + image_block + "\n\n"
        anchor = "\n## 核心能力"
        index = original.find(anchor)
        if index >= 0:
            updated = original[:index] + "\n" + section + original[index + 1 :]
        else:
            suffix = "" if original.endswith("\n") else "\n"
            updated = original + suffix + "\n" + section

    if updated == original:
        return False

    readme_path.write_text(updated, encoding="utf-8")
    return True


def main() -> int:
    args = parse_args()
    repo_root = Path(__file__).resolve().parents[1]
    readme_path = (repo_root / args.readme).resolve()

    try:
        views = normalize_views(args.views)
        if not args.skip_capture:
            run_capture(repo_root, args, views)

        block = build_image_block(repo_root, args.image_dir, views)
        changed = update_readme(repo_root, readme_path, block)
    except (ValueError, RuntimeError) as exc:
        print(f"[readme-images] error: {exc}", file=sys.stderr)
        return 1

    print("[readme-images] done")
    print(f"- readme: {readme_path.relative_to(repo_root)}")
    print(f"- updated: {'yes' if changed else 'no (already up-to-date)'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
