#!/usr/bin/env bash
# Explicit, opt-in OpenCode installation using the published npm package.
set -euo pipefail
# WSL appends the Windows PATH. Windows binaries (node, npm, opencode.exe) must never be
# mistaken for Linux ones, so drop /mnt/<drive>/ entries for this script.
PATH="$(printf '%s' "$PATH" | tr ':' '\n' | grep -v '^/mnt/[a-zA-Z]/' | paste -sd: -)"
export PATH
DRY_RUN=0
for arg in "$@"; do
  case "$arg" in
    --dry-run) DRY_RUN=1 ;;
    *) echo "Unknown option: $arg" >&2; exit 2 ;;
  esac
done
if (( DRY_RUN )); then
  echo "[dry-run] Check Node.js and npm availability"
  echo "[dry-run] npm install --global opencode-ai"
  echo "[dry-run] opencode --version"
  echo "[dry-run] No packages installed or modified."
  exit 0
fi
if [[ -s "$HOME/.nvm/nvm.sh" ]]; then
  . "$HOME/.nvm/nvm.sh"
fi
if ! command -v node >/dev/null 2>&1 || ! command -v npm >/dev/null 2>&1; then
  echo "Node.js and npm are required. Install a supported Node.js release first." >&2
  exit 1
fi
if command -v opencode >/dev/null 2>&1; then
  echo "OpenCode already available: $(command -v opencode)"
  opencode --version
  exit 0
fi
echo "Installing OpenCode via npm. This downloads a third-party package."
echo "Review the package and npm configuration before proceeding."
npm install --global opencode-ai
if ! command -v opencode >/dev/null 2>&1; then
  echo "Installation finished but opencode is not on PATH; check npm global bin path." >&2
  exit 1
fi
opencode --version
