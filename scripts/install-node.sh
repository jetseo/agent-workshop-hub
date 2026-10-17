#!/usr/bin/env bash
# Explicit opt-in Node.js prerequisite setup for OpenCode on Ubuntu/WSL.
set -euo pipefail
DRY_RUN=0
for arg in "$@"; do
  case "$arg" in
    --dry-run) DRY_RUN=1 ;;
    *) echo "Unknown option: $arg" >&2; exit 2 ;;
  esac
done
MIN_NODE_MAJOR=20
if (( DRY_RUN )); then
  echo "[dry-run] Check Node.js >= ${MIN_NODE_MAJOR} and npm availability"
  echo "[dry-run] If missing: sudo apt-get update && sudo apt-get install -y nodejs npm"
  echo "[dry-run] Verify node --version and npm --version"
  echo "[dry-run] No packages installed or modified."
  exit 0
fi
if [[ "$(uname -s)" != Linux ]]; then
  echo "Linux required" >&2; exit 1
fi
check_node() {
  command -v node >/dev/null 2>&1 &&
    command -v npm >/dev/null 2>&1 &&
    [[ "$(node --version)" =~ ^v([0-9]+)\. ]] &&
    (( ${BASH_REMATCH[1]} >= MIN_NODE_MAJOR ))
}
if check_node; then
  echo "Node.js ready: $(node --version), npm $(npm --version)"
  exit 0
fi
if ! command -v apt-get >/dev/null 2>&1; then
  echo "apt-get unavailable; install supported Node.js >= ${MIN_NODE_MAJOR} manually." >&2
  exit 1
fi
echo "Checking Ubuntu package candidate before installing."
sudo apt-get update
candidate="$(apt-cache policy nodejs | awk '/Candidate:/ {print $2; exit}')"
candidate_major="$(printf '%s' "$candidate" | sed -nE 's/^[0-9]*:([0-9]+)\..*/\1/p; s/^([0-9]+)\..*/\1/p')"
if [[ -z "$candidate_major" ]] || (( candidate_major < MIN_NODE_MAJOR )); then
  echo "Ubuntu nodejs candidate '$candidate' does not meet Node.js >= ${MIN_NODE_MAJOR}." >&2
  echo "No nodejs/npm packages were installed by this script." >&2
  echo "Install a supported Node.js release from the official Node.js distribution, then rerun." >&2
  exit 1
fi
echo "Installing Ubuntu nodejs and npm packages (candidate: $candidate)."
sudo apt-get install -y nodejs npm
if ! check_node; then
  echo "Distribution Node.js is too old or npm is missing." >&2
  echo "OpenCode requires Node.js >= ${MIN_NODE_MAJOR}; install a supported release from the official Node.js distribution, then rerun." >&2
  exit 1
fi
echo "Node.js ready: $(node --version), npm $(npm --version)"
