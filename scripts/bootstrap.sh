#!/usr/bin/env bash
set -euo pipefail
DRY_RUN=0
for arg in "$@"; do
  case "$arg" in
    --dry-run) DRY_RUN=1 ;;
    *) echo "Unknown argument: $arg" >&2; exit 2 ;;
  esac
done
if [[ "$(uname -s)" != Linux ]]; then echo "Linux required" >&2; exit 1; fi
if ! command -v apt-get >/dev/null; then echo "Ubuntu/Debian apt-get required" >&2; exit 1; fi
packages=(git curl ca-certificates python3 python3-venv python3-pip jq)
echo "JetAI Hub: prerequisite packages: ${packages[*]}"
if [[ "$DRY_RUN" == 1 ]]; then echo "[dry-run] sudo apt-get update && sudo apt-get install -y ${packages[*]}"; exit 0; fi
sudo apt-get update
sudo apt-get install -y "${packages[@]}"
echo "Base prerequisites installed."
echo "Next: review docs/setup.md before installing OpenCode or Ollama."
