#!/usr/bin/env bash
set -euo pipefail
case "${1:-status}" in
  start|stop|restart|status) exec systemctl --user "$1" rsi-web-demo.service ;;
  logs) exec journalctl --user -u rsi-web-demo.service -n 70 --no-pager ;;
  *) echo '用法：./manage.sh start|stop|restart|status|logs' >&2; exit 2 ;;
esac
