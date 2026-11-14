#!/usr/bin/env bash
# Deploy from your laptop:  ./deploy/push.sh <VPS_IP>
# First run also copies .env; afterwards installs deps and restarts the API.
set -euo pipefail
HOST="${1:?usage: deploy/push.sh <VPS_IP>}"
cd "$(dirname "$0")/.."

ssh "root@$HOST" 'mkdir -p /opt/candor/app'

rsync -az --delete \
  --exclude '.env' --exclude 'key' --exclude 'key.pub' --exclude '*.pem' --exclude '__pycache__' --exclude '.DS_Store' --exclude '.git' \
  --exclude 'test_1k.json' --exclude 'data/reddit-dataset-posts' \
  --exclude 'web/reports' --exclude 'data/live' --exclude 'narrative-buckets/out' --exclude 'narrative-buckets/venv' \
  ./ "root@$HOST:/opt/candor/app/"

# Only the keys the server needs go over SSH (never into git, never X creds).
# systemd's EnvironmentFile has no 'export' syntax, so strip it.
cat .env narrative-buckets/.env \
  | sed -E 's/^export +//' \
  | grep -E '^(CONTEXT_DEV_API_KEY|GEMINI_API_KEY|NVIDIA_API_KEY|NVIDIA_BASE_URL|NVIDIA_MODEL)=' \
  | ssh "root@$HOST" 'cat > /opt/candor/app/.env'

ssh "root@$HOST" '
  set -e
  if [ ! -f /etc/systemd/system/candor.service ]; then
    bash /opt/candor/app/deploy/setup_vps.sh
  else
    /opt/candor/venv/bin/pip install -q -r /opt/candor/app/requirements.txt
    chown -R candor:candor /opt/candor && chmod 600 /opt/candor/app/.env
    systemctl restart candor
    sleep 2 && curl -fsS http://127.0.0.1/api/health && echo "  <- Candor is up"
  fi
'
echo "Open http://$HOST/"
