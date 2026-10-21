#!/usr/bin/env bash
# Start the local dashboard and optionally an explicitly approved worker.
set -euo pipefail
cd "$(dirname "$0")/.."
# WSL appends the Windows PATH. Windows binaries (node, npm, opencode.exe) must never be
# mistaken for Linux ones, so drop /mnt/<drive>/ entries for this script.
PATH="$(printf '%s' "$PATH" | tr ':' '\n' | grep -v '^/mnt/[a-zA-Z]/' | paste -sd: -)"
export PATH
PORT=8787
ALLOW_OPENCODE=0
ENABLE_TERMINAL=0
for arg in "$@"; do
  case "$arg" in
    --allow-opencode) ALLOW_OPENCODE=1 ;;
    --enable-terminal) ENABLE_TERMINAL=1 ;;
    --port=*) PORT="${arg#--port=}" ;;
    *) echo "Unknown option: $arg" >&2; exit 2 ;;
  esac
done
if ! [[ "$PORT" =~ ^[0-9]+$ ]] || (( 10#$PORT < 1024 || 10#$PORT > 65535 )); then
  echo "Port must be 1024-65535" >&2; exit 2
fi
if [[ -s "$HOME/.nvm/nvm.sh" ]]; then
  . "$HOME/.nvm/nvm.sh"
fi
python3 -c 'import workshop.dashboard, workshop.worker' || {
  echo "Python application not importable. Run from a complete checkout." >&2
  exit 1
}
child_pid=""
cleanup() {
  if [[ -n "$child_pid" ]]; then
    kill "$child_pid" 2>/dev/null || true
    wait "$child_pid" 2>/dev/null || true
  fi
}
trap cleanup EXIT INT TERM
if (( ALLOW_OPENCODE )); then
  flags=()
  if (( ALLOW_OPENCODE )); then flags+=(--allow-opencode); fi
  python3 -m workshop.worker --watch "${flags[@]}" &
  child_pid=$!
  echo "Worker enabled: ${flags[*]}"
else
  echo "Dashboard-only mode: no AI jobs will be executed."
fi
echo "Open http://127.0.0.1:$PORT"
dashboard_flags=(--port "$PORT")
if (( ENABLE_TERMINAL )); then
  dashboard_flags+=(--enable-terminal)
  echo "Terminal enabled: a shell running as your user. Use only the private link printed below."
fi
python3 -m workshop.dashboard "${dashboard_flags[@]}"
