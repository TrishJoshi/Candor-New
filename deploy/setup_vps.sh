#!/usr/bin/env bash
# One-time server setup for a FRESH VPS. Run ON THE VPS as root (Ubuntu 24.04):
#   git clone https://github.com/TrishJoshi/Candor-New.git /opt/candor/app
#   bash /opt/candor/app/deploy/setup_vps.sh
# Afterwards deploy from your laptop with deploy/deploy.sh (see README).
set -euo pipefail

apt-get update
DEBIAN_FRONTEND=noninteractive apt-get install -y python3-venv python3-pip nginx git ufw

id candor &>/dev/null || useradd --system --home /opt/candor --shell /usr/sbin/nologin candor
mkdir -p /opt/candor/app
python3 -m venv /opt/candor/venv
/opt/candor/venv/bin/pip install --upgrade pip
/opt/candor/venv/bin/pip install -r /opt/candor/app/requirements.txt
chown -R candor:candor /opt/candor
chmod 600 /opt/candor/app/.env

cp /opt/candor/app/deploy/candor.service /etc/systemd/system/candor.service
systemctl daemon-reload
systemctl enable --now candor

# never clobber a live site (certbot edits it in place)
[ -f /etc/nginx/sites-available/candor ] || cp /opt/candor/app/deploy/nginx-candor.conf /etc/nginx/sites-available/candor
ln -sf /etc/nginx/sites-available/candor /etc/nginx/sites-enabled/candor
rm -f /etc/nginx/sites-enabled/default
nginx -t && systemctl enable nginx && systemctl restart nginx

ufw allow OpenSSH
ufw allow 'Nginx Full'
ufw --force enable

sleep 2
curl -fsS http://127.0.0.1/api/health && echo "  <- Candor is up"
