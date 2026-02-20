#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
RUN_DIR="$ROOT_DIR/.run"

VENV_DIR="$ROOT_DIR/.venv"
BACK_PID_FILE="$RUN_DIR/backend.pid"
FRONT_PID_FILE="$RUN_DIR/frontend.pid"
BACK_LOG_FILE="$RUN_DIR/backend.log"
FRONT_LOG_FILE="$RUN_DIR/frontend.log"

BACK_REQ_HASH_FILE="$RUN_DIR/backend_requirements.sha256"
FRONT_REQ_HASH_FILE="$RUN_DIR/frontend_dependencies.sha256"

BACK_HOST="${BACK_HOST:-0.0.0.0}"
BACK_PORT="${BACK_PORT:-18011}"
BACK_RELOAD="${BACK_RELOAD:-true}"
FRONT_HOST="${FRONT_HOST:-0.0.0.0}"
FRONT_PORT="${FRONT_PORT:-5173}"

FORCE_INSTALL=0
INSTALL_PLAYWRIGHT=0
COMMAND="up"

usage() {
  cat <<'EOF'
用法:
  ./dev.sh [up|down|restart|status] [--install] [--install-playwright]

命令:
  up        启动服务（默认）
  down      停止服务
  restart   重启服务
  status    查看服务状态

参数:
  --install             强制重装后端/前端依赖
  --install-playwright  安装 Chromium（用于 PDF 导出）
  -h, --help            显示帮助

环境变量:
  BACK_RELOAD=true|false  是否开启后端热更新（默认 true）
EOF
}

info() {
  echo "[dev] $*"
}

warn() {
  echo "[dev][warn] $*"
}

fail() {
  echo "[dev][error] $*"
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

  local pid=""
  IFS= read -r pid <"$pid_file" || true
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

stop_by_pid_file() {
  local pid_file="$1"
  local name="$2"

  if [[ ! -f "$pid_file" ]]; then
    info "$name 未运行"
    return
  fi

  local pid=""
  IFS= read -r pid <"$pid_file" || true
  if [[ -z "$pid" ]]; then
    rm -f "$pid_file"
    warn "$name pid 文件无效，已清理"
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
    for ((i = 1; i <= 5; i++)); do
      if ! kill -0 "$pid" >/dev/null 2>&1; then
        break
      fi
      sleep 1
    done

    if kill -0 "$pid" >/dev/null 2>&1; then
      kill -9 "$pid" >/dev/null 2>&1 || true
    fi

    info "已停止 $name (PID $pid)"
  else
    warn "$name 进程不存在，清理 pid 文件"
  fi

  rm -f "$pid_file"
}

wait_for_url() {
  local url="$1"
  local name="$2"
  local max_try="${3:-45}"

  local i
  for ((i = 1; i <= max_try; i++)); do
    if curl -fsS "$url" >/dev/null 2>&1; then
      info "$name 启动成功: $url"
      return 0
    fi
    sleep 1
  done

  warn "$name 启动超时，请检查日志: $RUN_DIR"
  return 1
}

hash_file() {
  local path="$1"
  python3 - "$path" <<'PY'
import hashlib
import pathlib
import sys

file_path = pathlib.Path(sys.argv[1])
hasher = hashlib.sha256()
with file_path.open("rb") as fp:
    for chunk in iter(lambda: fp.read(1024 * 1024), b""):
        hasher.update(chunk)
print(hasher.hexdigest())
PY
}

read_marker() {
  local marker_file="$1"
  local value=""
  if [[ -f "$marker_file" ]]; then
    IFS= read -r value <"$marker_file" || true
  fi
  printf '%s' "$value"
}

write_marker() {
  local marker_file="$1"
  local value="$2"
  printf '%s\n' "$value" >"$marker_file"
}

frontend_manifest_file() {
  if [[ -f "$ROOT_DIR/web/package-lock.json" ]]; then
    printf '%s' "$ROOT_DIR/web/package-lock.json"
  else
    printf '%s' "$ROOT_DIR/web/package.json"
  fi
}

backend_requirements_signature() {
  hash_file "$ROOT_DIR/requirements.txt"
}

frontend_requirements_signature() {
  local manifest
  manifest="$(frontend_manifest_file)"
  printf '%s:%s' "$(basename "$manifest")" "$(hash_file "$manifest")"
}

needs_backend_install() {
  if [[ "$FORCE_INSTALL" -eq 1 ]]; then
    return 0
  fi
  if [[ ! -x "$VENV_DIR/bin/python" || ! -x "$VENV_DIR/bin/uvicorn" ]]; then
    return 0
  fi

  local current_signature
  local saved_signature
  current_signature="$(backend_requirements_signature)"
  saved_signature="$(read_marker "$BACK_REQ_HASH_FILE")"

  [[ "$current_signature" != "$saved_signature" ]]
}

needs_frontend_install() {
  if [[ "$FORCE_INSTALL" -eq 1 ]]; then
    return 0
  fi
  if [[ ! -d "$ROOT_DIR/web/node_modules" || ! -x "$ROOT_DIR/web/node_modules/.bin/vite" ]]; then
    return 0
  fi

  local current_signature
  local saved_signature
  current_signature="$(frontend_requirements_signature)"
  saved_signature="$(read_marker "$FRONT_REQ_HASH_FILE")"

  [[ "$current_signature" != "$saved_signature" ]]
}

ensure_env_files() {
  if [[ ! -f "$ROOT_DIR/.env" && -f "$ROOT_DIR/.env.example" ]]; then
    info "创建后端 .env"
    cp "$ROOT_DIR/.env.example" "$ROOT_DIR/.env"
  fi

  if [[ ! -f "$ROOT_DIR/web/.env" && -f "$ROOT_DIR/web/.env.example" ]]; then
    info "创建前端 .env"
    cp "$ROOT_DIR/web/.env.example" "$ROOT_DIR/web/.env"
  fi
}

install_backend_deps_if_needed() {
  if [[ ! -d "$VENV_DIR" ]]; then
    info "创建 Python 虚拟环境..."
    python3 -m venv "$VENV_DIR"
  fi

  if needs_backend_install; then
    info "安装/更新后端依赖..."
    "$VENV_DIR/bin/pip" install -r "$ROOT_DIR/requirements.txt"
    write_marker "$BACK_REQ_HASH_FILE" "$(backend_requirements_signature)"
  else
    info "后端依赖已是最新，跳过安装"
  fi

  if [[ "$INSTALL_PLAYWRIGHT" -eq 1 ]]; then
    info "安装 Playwright Chromium..."
    "$VENV_DIR/bin/playwright" install chromium
  fi
}

install_frontend_deps_if_needed() {
  if needs_frontend_install; then
    info "安装/更新前端依赖..."
    npm --prefix "$ROOT_DIR/web" install
    write_marker "$FRONT_REQ_HASH_FILE" "$(frontend_requirements_signature)"
  else
    info "前端依赖已是最新，跳过安装"
  fi
}

start_backend() {
  if is_running "$BACK_PID_FILE"; then
    local pid=""
    IFS= read -r pid <"$BACK_PID_FILE" || true
    info "后端已在运行 (PID $pid)"
    return
  fi

  info "启动后端服务..."
  local uvicorn_cmd=(
    "$VENV_DIR/bin/uvicorn"
    app.main:app
    --host
    "$BACK_HOST"
    --port
    "$BACK_PORT"
  )

  local reload_flag
  reload_flag="$(printf '%s' "$BACK_RELOAD" | tr '[:upper:]' '[:lower:]')"
  if [[ "$reload_flag" == "1" || "$reload_flag" == "true" || "$reload_flag" == "yes" ]]; then
    uvicorn_cmd+=(--reload)
  fi

  nohup "${uvicorn_cmd[@]}" >"$BACK_LOG_FILE" 2>&1 &
  echo "$!" >"$BACK_PID_FILE"
}

start_frontend() {
  if is_running "$FRONT_PID_FILE"; then
    local pid=""
    IFS= read -r pid <"$FRONT_PID_FILE" || true
    info "前端已在运行 (PID $pid)"
    return
  fi

  info "启动前端服务..."
  nohup "$ROOT_DIR/web/node_modules/.bin/vite" \
    "$ROOT_DIR/web" \
    --config "$ROOT_DIR/web/vite.config.js" \
    --host "$FRONT_HOST" \
    --port "$FRONT_PORT" \
    >"$FRONT_LOG_FILE" 2>&1 &
  echo "$!" >"$FRONT_PID_FILE"
}

show_status() {
  local back_status="stopped"
  local front_status="stopped"

  if is_running "$BACK_PID_FILE"; then
    back_status="running (PID $(read_marker "$BACK_PID_FILE"))"
  fi
  if is_running "$FRONT_PID_FILE"; then
    front_status="running (PID $(read_marker "$FRONT_PID_FILE"))"
  fi

  echo "后端: $back_status"
  echo "前端: $front_status"
  echo "日志: $RUN_DIR"
}

do_up() {
  require_cmd python3
  require_cmd node
  require_cmd npm
  require_cmd curl

  mkdir -p "$RUN_DIR"
  ensure_env_files
  install_backend_deps_if_needed
  install_frontend_deps_if_needed

  start_backend
  start_frontend

  wait_for_url "http://127.0.0.1:${BACK_PORT}/" "后端"
  wait_for_url "http://127.0.0.1:${FRONT_PORT}/" "前端"

  echo
  info "全部启动完成"
  echo "  前端: http://127.0.0.1:${FRONT_PORT}"
  echo "  后端: http://127.0.0.1:${BACK_PORT}"
  echo "  文档: http://127.0.0.1:${BACK_PORT}/docs"
  echo "  日志: $RUN_DIR"
  echo
  echo "停止服务: ./dev.sh down"
  echo "重启服务: ./dev.sh restart"
}

do_down() {
  mkdir -p "$RUN_DIR"
  stop_by_pid_file "$FRONT_PID_FILE" "前端"
  stop_by_pid_file "$BACK_PID_FILE" "后端"
  info "停止完成"
}

do_restart() {
  do_down
  do_up
}

if [[ $# -gt 0 ]]; then
  case "$1" in
    up|down|restart|status)
      COMMAND="$1"
      shift
      ;;
    -h|--help|help)
      usage
      exit 0
      ;;
  esac
fi

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
      usage
      exit 0
      ;;
    *)
      fail "未知参数: $1"
      ;;
  esac
done

case "$COMMAND" in
  up)
    do_up
    ;;
  down)
    do_down
    ;;
  restart)
    do_restart
    ;;
  status)
    show_status
    ;;
  *)
    usage
    exit 1
    ;;
esac
