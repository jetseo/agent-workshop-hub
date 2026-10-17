#!/usr/bin/env bash
set -euo pipefail
bash -n scripts/bootstrap.sh
python3 -m compileall -q workshop tests
python3 -m unittest discover -s tests -v
python3 -m workshop.doctor
