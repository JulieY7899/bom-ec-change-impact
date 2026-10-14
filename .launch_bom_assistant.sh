#!/bin/zsh

set -u

PROJECT_DIR="$(cd "$(dirname "$0")" && pwd)"
APP_URL="http://127.0.0.1:8765"
HEALTH_URL="$APP_URL/api/health"
LOG_FILE="$PROJECT_DIR/data/server.log"
URL_FILE="$PROJECT_DIR/data/server.url"
QUIET_MODE="${1:-}"

show_error() {
  local message="$1"
  if [[ "$QUIET_MODE" == "--quiet" ]]; then
    /usr/bin/osascript -e "display alert \"BOM 助手启动失败\" message \"$message\" as critical" >/dev/null 2>&1
  else
    echo ""
    echo "$message"
    echo "日志位置：$LOG_FILE"
    echo ""
    read -k 1 "?按任意键关闭..."
  fi
}

is_ready() {
  is_ready_at "$APP_URL"
}

is_ready_at() {
  local candidate_url="$1"
  /usr/bin/curl -fsS --max-time 2 "$candidate_url/api/health" 2>/dev/null |
    /usr/bin/grep -q '"service": "bom-change-assistant"'
}

if [[ -r "$URL_FILE" ]]; then
  CACHED_URL="$(/bin/cat "$URL_FILE" 2>/dev/null || true)"
  if [[ -n "$CACHED_URL" ]] && is_ready_at "$CACHED_URL"; then
    /usr/bin/open "$CACHED_URL"
    exit 0
  fi
fi

if is_ready; then
  /usr/bin/open "$APP_URL"
  exit 0
fi

PYTHON_BIN="$(command -v python3 2>/dev/null || true)"
if [[ -z "$PYTHON_BIN" ]]; then
  show_error "未找到 Python 3。请安装 Python 3.11 或更高版本。"
  exit 1
fi

if ! "$PYTHON_BIN" -c 'import sys; raise SystemExit(0 if sys.version_info >= (3, 11) else 1)'; then
  show_error "当前 Python 版本低于 3.11，请升级后重试。"
  exit 1
fi

find_available_port() {
  "$PYTHON_BIN" - "$BASE_PORT" <<'PY'
import socket
import sys

start = int(sys.argv[1])
for candidate in range(start, start + 50):
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        try:
            sock.bind(("127.0.0.1", candidate))
        except OSError:
            continue
    print(candidate)
    raise SystemExit(0)
raise SystemExit("没有找到可用的本地端口")
PY
}

BASE_PORT="${BOM_ASSISTANT_PORT:-8765}"
PORT="$(find_available_port)"
APP_URL="http://127.0.0.1:$PORT"
HEALTH_URL="$APP_URL/api/health"
/bin/printf '%s\n' "$APP_URL" >"$URL_FILE"

/bin/mkdir -p "$PROJECT_DIR/data"

if [[ "$QUIET_MODE" == "--quiet" ]]; then
  echo "[$(/bin/date '+%Y-%m-%d %H:%M:%S')] 正在启动 BOM 助手" >>"$LOG_FILE"
  "$PYTHON_BIN" "$PROJECT_DIR/app.py" --host 127.0.0.1 --port "$PORT" --no-browser >>"$LOG_FILE" 2>&1 &
  SERVER_PID=$!
  for _ in {1..30}; do
    if is_ready; then
      /usr/bin/open "$APP_URL"
      exit 0
    fi
    if ! kill -0 "$SERVER_PID" 2>/dev/null; then
      break
    fi
    /bin/sleep 0.2
  done
  /bin/rm -f "$URL_FILE"
  show_error "BOM 助手未能启动，请检查日志。"
  exit 1
fi

exec "$PYTHON_BIN" "$PROJECT_DIR/app.py" --host 127.0.0.1 --port "$PORT"
