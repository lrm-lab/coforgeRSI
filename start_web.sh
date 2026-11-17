#!/usr/bin/env bash
set -euo pipefail
rsi_web_root="$(cd -- "$(dirname -- "$0")" && pwd)"
exec python3 -u "${rsi_web_root}/server.py" --host "${RSI_WEB_HOST:-127.0.0.1}" --port "${RSI_WEB_PORT:-18766}" "$@"
