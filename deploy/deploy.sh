#!/usr/bin/env bash
# Deploy what is pushed to GitHub (origin/main) to the VPS.
#
#   ./deploy/deploy.sh            pull + install deps + restart + health check
#   ./deploy/deploy.sh --env      also send API keys from the local .env files first
#
# The server's /opt/candor/app is a git clone of this repo (owned by user "candor").
# Never edit files there by hand: commit here, push, deploy. Secrets (.env) and data/
# are git-ignored and live only on the server. nginx and certbot are not touched.
set -euo pipefail
HOST="${CANDOR_HOST:-root@2.25.6.246}"
cd "$(dirname "$0")/.."

[ -z "$(git status --porcelain --untracked-files=no)" ] || { echo "Commit your changes first."; exit 1; }
git fetch -q origin
[ "$(git rev-parse HEAD)" = "$(git rev-parse origin/main)" ] || { echo "Push first: local HEAD != origin/main."; exit 1; }

if [ "${1:-}" = "--env" ]; then
  # Only the keys the server needs, sent over SSH stdin (never as command-line arguments).
  KEYS='CONTEXT_DEV_API_KEY|GEMINI_API_KEY|NVIDIA_API_KEY|NVIDIA_BASE_URL|NVIDIA_MODEL'
  cat .env narrative-buckets/.env 2>/dev/null | sed -E 's/^export +//' | grep -E "^($KEYS)=" \
    | ssh "$HOST" "set -e; f=/opt/candor/app/.env; new=\$(mktemp); cat > \$new
        { grep -vE '^($KEYS)=' \$f 2>/dev/null || true; cat \$new; } > \$f.tmp
        mv \$f.tmp \$f; rm -f \$new; chown candor:candor \$f; chmod 600 \$f
        echo 'env keys on server:'; sed -E 's/=.*/=<set>/' \$f"
fi

ssh "$HOST" 'set -e
  cd /opt/candor/app
  runuser -u candor -- git pull --ff-only -q
  /opt/candor/venv/bin/pip install -q -r requirements.txt
  systemctl restart candor
  for i in $(seq 1 40); do curl -fsS -m 2 http://127.0.0.1:8000/api/health >/dev/null 2>&1 && break; sleep 1; done
  curl -fsS http://127.0.0.1:8000/api/health && echo "  <- API up at $(git log --oneline -1)"'
