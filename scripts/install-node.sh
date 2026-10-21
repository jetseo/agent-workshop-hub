#!/usr/bin/env bash
# Explicit opt-in Node.js prerequisite setup for OpenCode on Ubuntu/WSL.
set -Eeuo pipefail
trap 'echo "install-node.sh: command failed at line $LINENO: $BASH_COMMAND" >&2' ERR
# WSL appends the Windows PATH. Windows binaries (node, npm, opencode.exe) must never be
# mistaken for Linux ones, so drop /mnt/<drive>/ entries for this script.
PATH="$(printf '%s' "$PATH" | tr ':' '\n' | grep -v '^/mnt/[a-zA-Z]/' | paste -sd: -)"
export PATH
DRY_RUN=0
USE_NVM=0
for arg in "$@"; do
  case "$arg" in
    --dry-run) DRY_RUN=1 ;;
    --use-nvm) USE_NVM=1 ;;
    *) echo "Unknown option: $arg" >&2; exit 2 ;;
  esac
done
MIN_NODE_MAJOR=20
NVM_VERSION=v0.40.3
NODE_MAJOR=22
if (( DRY_RUN )); then
  echo "[dry-run] Check Node.js >= ${MIN_NODE_MAJOR} and npm availability"
  echo "[dry-run] If missing: sudo apt-get update && sudo apt-get install -y nodejs npm"
  if (( USE_NVM )); then echo "[dry-run] If apt candidate is too old, install nvm $NVM_VERSION from GitHub and Node.js $NODE_MAJOR"; fi
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
if [[ -s "$HOME/.nvm/nvm.sh" ]]; then
  # Non-interactive WSL sessions do not source .bashrc.
  . "$HOME/.nvm/nvm.sh"
fi
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
policy_output="$(apt-cache policy nodejs 2>&1 || true)"
# No early-exit awk: avoids SIGPIPE turning a successful lookup into a silent failure.
candidate="$(awk '/Candidate:/ && !seen {print $2; seen=1}' <<<"$policy_output")"
candidate_major="$(printf '%s' "$candidate" | sed -nE 's/^([0-9]+:)?([0-9]+)\..*/\2/p')"
if [[ -z "$candidate_major" ]] || (( candidate_major < MIN_NODE_MAJOR )); then
  echo "Ubuntu nodejs candidate '$candidate' does not meet Node.js >= ${MIN_NODE_MAJOR}." >&2
  if (( ! USE_NVM )); then
    echo "Use --use-nvm to install Node.js $NODE_MAJOR in the current user's home directory." >&2
    exit 1
  fi
  if ! command -v git >/dev/null 2>&1; then
    echo "git is required for nvm setup; run the Ubuntu bootstrap first." >&2
    exit 1
  fi
  if [[ ! -d "$HOME/.nvm/.git" ]]; then
    if [[ -e "$HOME/.nvm" ]]; then
      echo "Existing ~/.nvm is not a git checkout; refusing to overwrite." >&2
      exit 1
    fi
    git clone --branch "$NVM_VERSION" --depth 1 https://github.com/nvm-sh/nvm.git "$HOME/.nvm"
  fi
  if [[ ! -s "$HOME/.nvm/nvm.sh" ]]; then
    echo "nvm.sh missing; refusing to continue." >&2
    exit 1
  fi
  . "$HOME/.nvm/nvm.sh"
  nvm install "$NODE_MAJOR"
  nvm alias default "$NODE_MAJOR"
  if ! check_node; then echo "Node.js validation failed after nvm install." >&2; exit 1; fi
  echo "Node.js ready via nvm: $(node --version), npm $(npm --version)"
  exit 0
fi
echo "Installing Ubuntu nodejs and npm packages (candidate: $candidate)."
sudo apt-get install -y nodejs npm
if ! check_node; then
  echo "Distribution Node.js is too old or npm is missing." >&2
  echo "OpenCode requires Node.js >= ${MIN_NODE_MAJOR}; install a supported release from the official Node.js distribution, then rerun." >&2
  exit 1
fi
echo "Node.js ready: $(node --version), npm $(npm --version)"
