#!/usr/bin/env bash
set -euo pipefail
rsi_demo_root="$(cd -- "$(dirname -- "$0")" && pwd)"
exec python3 -u "${rsi_demo_root}/run_live.py" "$@"
