#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
RUN_DIR="$ROOT_DIR/.run"

BACK_PID_FILE="$RUN_DIR/backend.pid"
FRONT_PID_FILE="$RUN_DIR/frontend.pid"

stop_by_pid_file() {
  local pid_file="$1"
  local name="$2"

  if [[ ! -f "$pid_file" ]]; then
    echo "[dev-down] $name 未运行"
    return
  fi

  local pid
  pid="$(cat "$pid_file" 2>/dev/null || true)"
  if [[ -z "$pid" ]]; then
    rm -f "$pid_file"
    echo "[dev-down] $name pid 文件无效，已清理"
    return
  fi

  if kill -0 "$pid" >/dev/null 2>&1; then
    local child_pids
    child_pids="$(pgrep -P "$pid" 2>/dev/null || true)"
    if [[ -n "$child_pids" ]]; then
      # shellcheck disable=SC2086
      kill $child_pids >/dev/null 2>&1 || true
    fi

    kill "$pid" >/dev/null 2>&1 || true

    local i
    for ((i=1; i<=5; i++)); do
      if ! kill -0 "$pid" >/dev/null 2>&1; then
        break
      fi
      sleep 1
    done

    if kill -0 "$pid" >/dev/null 2>&1; then
      kill -9 "$pid" >/dev/null 2>&1 || true
    fi

    echo "[dev-down] 已停止 $name (PID $pid)"
  else
    echo "[dev-down] $name 进程不存在，清理 pid 文件"
  fi

  rm -f "$pid_file"
}

stop_by_pid_file "$FRONT_PID_FILE" "前端"
stop_by_pid_file "$BACK_PID_FILE" "后端"

echo "[dev-down] 完成"
