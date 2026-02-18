#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
RUN_DIR="$ROOT_DIR/.run"

BACK_PID_FILE="$RUN_DIR/backend.pid"
FRONT_PID_FILE="$RUN_DIR/frontend.pid"
BACK_LOG_FILE="$RUN_DIR/backend.log"
FRONT_LOG_FILE="$RUN_DIR/frontend.log"

BACK_HOST="${BACK_HOST:-0.0.0.0}"
BACK_PORT="${BACK_PORT:-18011}"
FRONT_HOST="${FRONT_HOST:-0.0.0.0}"
FRONT_PORT="${FRONT_PORT:-5173}"

FORCE_INSTALL=0
INSTALL_PLAYWRIGHT=0

while [[ $# -gt 0 ]]; do
  case "$1" in
    --install)
      FORCE_INSTALL=1
      shift
      ;;
    --install-playwright)
      INSTALL_PLAYWRIGHT=1
      shift
      ;;
    -h|--help)
      cat <<'EOF'
用法:
  ./scripts/dev-up.sh [--install] [--install-playwright]

参数:
  --install             强制重新安装后端/前端依赖
  --install-playwright  安装 Chromium 浏览器(用于 PDF 导出)
EOF
      exit 0
      ;;
    *)
      echo "未知参数: $1"
      exit 1
      ;;
  esac
done

info() {
  echo "[dev-up] $*"
}

warn() {
  echo "[dev-up][warn] $*"
}

fail() {
  echo "[dev-up][error] $*"
  exit 1
}

require_cmd() {
  local cmd="$1"
  command -v "$cmd" >/dev/null 2>&1 || fail "缺少命令: $cmd"
}

is_running() {
  local pid_file="$1"
  if [[ ! -f "$pid_file" ]]; then
    return 1
  fi

  local pid
  pid="$(cat "$pid_file" 2>/dev/null || true)"
  if [[ -z "$pid" ]]; then
    rm -f "$pid_file"
    return 1
  fi

  if kill -0 "$pid" >/dev/null 2>&1; then
    return 0
  fi

  rm -f "$pid_file"
  return 1
}

wait_for_url() {
  local url="$1"
  local name="$2"
  local max_try="${3:-45}"

  local i
  for ((i=1; i<=max_try; i++)); do
    if curl -fsS "$url" >/dev/null 2>&1; then
      info "$name 启动成功: $url"
      return 0
    fi
    sleep 1
  done

  warn "$name 启动超时，请检查日志: $RUN_DIR"
  return 1
}

require_cmd python3
require_cmd node
require_cmd npm
require_cmd curl

mkdir -p "$RUN_DIR"

if [[ ! -d "$ROOT_DIR/.venv" ]]; then
  info "创建 Python 虚拟环境..."
  python3 -m venv "$ROOT_DIR/.venv"
fi

if [[ "$FORCE_INSTALL" -eq 1 || ! -x "$ROOT_DIR/.venv/bin/uvicorn" ]]; then
  info "安装后端依赖..."
  "$ROOT_DIR/.venv/bin/pip" install -r "$ROOT_DIR/requirements.txt"
fi

if [[ "$INSTALL_PLAYWRIGHT" -eq 1 ]]; then
  info "安装 Playwright Chromium..."
  "$ROOT_DIR/.venv/bin/playwright" install chromium
fi

if [[ ! -f "$ROOT_DIR/.env" ]]; then
  info "创建后端 .env"
  cp "$ROOT_DIR/.env.example" "$ROOT_DIR/.env"
fi

if [[ "$FORCE_INSTALL" -eq 1 || ! -d "$ROOT_DIR/web/node_modules" ]]; then
  info "安装前端依赖..."
  npm --prefix "$ROOT_DIR/web" install
fi

if [[ ! -f "$ROOT_DIR/web/.env" ]]; then
  info "创建前端 .env"
  cp "$ROOT_DIR/web/.env.example" "$ROOT_DIR/web/.env"
fi

if is_running "$BACK_PID_FILE"; then
  info "后端已在运行 (PID $(cat "$BACK_PID_FILE"))"
else
  info "启动后端服务..."
  nohup "$ROOT_DIR/.venv/bin/uvicorn" app.main:app \
    --host "$BACK_HOST" --port "$BACK_PORT" --reload \
    >"$BACK_LOG_FILE" 2>&1 &
  echo "$!" > "$BACK_PID_FILE"
fi

if is_running "$FRONT_PID_FILE"; then
  info "前端已在运行 (PID $(cat "$FRONT_PID_FILE"))"
else
  info "启动前端服务..."
  nohup "$ROOT_DIR/web/node_modules/.bin/vite" \
    "$ROOT_DIR/web" \
    --config "$ROOT_DIR/web/vite.config.js" \
    --host "$FRONT_HOST" \
    --port "$FRONT_PORT" \
    >"$FRONT_LOG_FILE" 2>&1 &
  echo "$!" > "$FRONT_PID_FILE"
fi

wait_for_url "http://127.0.0.1:${BACK_PORT}/" "后端"
wait_for_url "http://127.0.0.1:${FRONT_PORT}/" "前端"

echo
info "全部启动完成"
echo "  前端: http://127.0.0.1:${FRONT_PORT}"
echo "  后端: http://127.0.0.1:${BACK_PORT}"
echo "  文档: http://127.0.0.1:${BACK_PORT}/docs"
echo "  日志: $RUN_DIR"
echo
echo "停止服务: ./scripts/dev-down.sh"
