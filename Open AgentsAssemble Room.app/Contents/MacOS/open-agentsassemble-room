#!/bin/zsh
set -u

APP_DIR="$(cd "$(dirname "$0")/../.." && pwd)"
ROOT="$(cd "$APP_DIR/.." && pwd)"
PORT="${AGENTSASSEMBLE_PORT:-8765}"
URL="http://127.0.0.1:${PORT}"
STATE_DIR="$ROOT/.agentsassemble"
LOG="$STATE_DIR/gui-launcher.log"
PID_FILE="$STATE_DIR/gui-launcher.pid"
HOST_TOKEN_FILE="$STATE_DIR/host-token"

export PATH="/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin"

show_failure() {
  /usr/bin/osascript - "$LOG" <<'OSA'
on run argv
  display dialog "AgentsAssemble room could not start. Log: " & (item 1 of argv) buttons {"OK"} default button "OK" with icon caution
end run
OSA
}

public_tunnel_enabled() {
  [[ "${AGENTSASSEMBLE_PUBLIC_TUNNEL:-1}" != "0" ]]
}

ensure_host_token() {
  if [[ -n "${AGENTSASSEMBLE_HOST_TOKEN:-}" ]]; then
    return
  fi
  if [[ -f "$HOST_TOKEN_FILE" ]]; then
    AGENTSASSEMBLE_HOST_TOKEN="$(< "$HOST_TOKEN_FILE")"
    export AGENTSASSEMBLE_HOST_TOKEN
    return
  fi
  local token
  token="$(python3 - <<'PY'
import secrets
print(secrets.token_urlsafe(32))
PY
)"
  umask 077
  /usr/bin/printf '%s\n' "$token" > "$HOST_TOKEN_FILE"
  AGENTSASSEMBLE_HOST_TOKEN="$token"
  export AGENTSASSEMBLE_HOST_TOKEN
}

server_status() {
  /usr/bin/curl -fsS "$URL/api/public-invite/status" 2>/dev/null
}

server_public_ready() {
  local status_json
  status_json="$(server_status)" || return 1
  if ! public_tunnel_enabled; then
    return 0
  fi
  /usr/bin/python3 - "$status_json" <<'PY'
import json
import sys

try:
    status = json.loads(sys.argv[1])
except Exception:
    raise SystemExit(1)
url = str(status.get("public_url") or "")
phase = str((status.get("tunnel") or {}).get("phase") or "")
raise SystemExit(0 if url.startswith("https://") and phase == "running" else 1)
PY
}

start_existing_public_tunnel() {
  public_tunnel_enabled || return 1
  ensure_host_token
  /usr/bin/curl -fsS \
    -X POST \
    -H "Content-Type: application/json" \
    -H "X-Host-Token: ${AGENTSASSEMBLE_HOST_TOKEN}" \
    -d '{}' \
    "$URL/api/public-invite/tunnel/start" >/dev/null 2>&1 || return 1
  for _ in {1..60}; do
    if server_public_ready; then
      return 0
    fi
    /bin/sleep 0.5
  done
  return 1
}

open_room() {
  if [[ "${AGENTSASSEMBLE_LAUNCHER_NO_OPEN:-}" == "1" ]]; then
    echo "$URL/"
    return
  fi
  /usr/bin/open "$URL/"
}

mkdir -p "$STATE_DIR"
ensure_host_token

if server_public_ready; then
  open_room
  exit 0
fi

if server_status >/dev/null 2>&1; then
  if start_existing_public_tunnel; then
    open_room
    exit 0
  fi
fi

if [[ ! -f "$ROOT/frontend/dist/index.html" ]]; then
  {
    echo "[$(/bin/date -u '+%Y-%m-%dT%H:%M:%SZ')] Building frontend..."
    cd "$ROOT" && npm --prefix frontend run build
  } >> "$LOG" 2>&1 || {
    show_failure
    exit 1
  }
fi

(
  cd "$ROOT" || exit 1
  args=(
    python3 -u -m agentsassemble.cli gui
    --host 127.0.0.1
    --port "$PORT"
    --output-root "$STATE_DIR"
    --host-token "$AGENTSASSEMBLE_HOST_TOKEN"
  )
  if public_tunnel_enabled; then
    args+=(--start-public-tunnel)
  fi
  python3 - "$LOG" "$PID_FILE" "${args[@]}" <<'PY'
import subprocess
import sys
from pathlib import Path

log_path = Path(sys.argv[1])
pid_path = Path(sys.argv[2])
command = sys.argv[3:]
with log_path.open("ab", buffering=0) as log:
    process = subprocess.Popen(
        command,
        stdin=subprocess.DEVNULL,
        stdout=log,
        stderr=subprocess.STDOUT,
        start_new_session=True,
        close_fds=True,
    )
pid_path.write_text(f"{process.pid}\n", encoding="utf-8")
PY
)

for _ in {1..60}; do
  if server_public_ready; then
    open_room
    exit 0
  fi
  /bin/sleep 0.5
done

show_failure
exit 1
