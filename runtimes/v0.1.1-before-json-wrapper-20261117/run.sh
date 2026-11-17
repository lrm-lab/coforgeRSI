#!/usr/bin/env bash
set -euo pipefail
rsi_package_root="$(cd -- "$(dirname -- "$0")" && pwd)"
export PYTHONPATH="${rsi_package_root}${PYTHONPATH:+:${PYTHONPATH}}"
exec python3 -m general_rsi "$@"
