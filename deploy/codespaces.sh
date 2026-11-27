#!/usr/bin/env bash
# 在 GitHub Codespaces 的终端里运行：bash deploy/codespaces.sh
# 第一次会让你设口令（≥10 位，丢了找不回）；之后直接启动服务并打印可以在 iPad 上打开的地址。
set -euo pipefail
DB="${HOME}/kagami.db"
[ -f "$DB" ] || python -m people_reading.server --db "$DB" --init
exec python -m people_reading.server --db "$DB"
