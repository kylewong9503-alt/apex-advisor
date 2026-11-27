#!/usr/bin/env bash
# 在你的服务器上以 root 运行：bash deploy/install.sh [域名]
# 不给域名 = 只监听本机，用 SSH 隧道访问（推荐先这样试）；给域名 = 同时装 Caddy 提供 HTTPS。
set -euo pipefail
DOMAIN="${1:-}"
id kagami >/dev/null 2>&1 || useradd --system --home /opt/kagami --shell /usr/sbin/nologin kagami
mkdir -p /opt/kagami /var/lib/kagami
cp -r people_reading requirements.txt /opt/kagami/
python3 -m venv /opt/kagami/venv
/opt/kagami/venv/bin/pip install -q -r /opt/kagami/requirements.txt
chown -R kagami:kagami /opt/kagami /var/lib/kagami
chmod 700 /var/lib/kagami
if [ ! -f /var/lib/kagami/kagami.db ]; then
  echo "== 创建加密库（口令至少 10 位，丢了无法找回）=="
  sudo -u kagami /opt/kagami/venv/bin/python -m people_reading.server --db /var/lib/kagami/kagami.db --init
fi
[ -f /etc/kagami.env ] || { install -m 600 /dev/null /etc/kagami.env; printf "# PR_PROVIDER=deepseek\n# PR_ALLOW_EXTERNAL=1\n# DEEPSEEK_API_KEY=\n" > /etc/kagami.env; }
sed "s/__DOMAIN__/${DOMAIN:-localhost}/" deploy/kagami.service > /etc/systemd/system/kagami.service
systemctl daemon-reload && systemctl enable --now kagami
if [ -n "$DOMAIN" ]; then
  command -v caddy >/dev/null || { apt-get update -q && apt-get install -y -q caddy; }
  sed "s/__DOMAIN__/$DOMAIN/" deploy/Caddyfile > /etc/caddy/Caddyfile && systemctl reload caddy
  echo "完成：https://$DOMAIN"
else
  echo "完成。在你自己的电脑上运行： ssh -L 8765:127.0.0.1:8765 <你的服务器>  然后打开 http://localhost:8765"
fi
