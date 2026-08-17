#!/usr/bin/env bash
# Run INSIDE the Blackstart LXC as root after the repo is at /opt/blackstart.
set -euo pipefail

APP_DIR="${APP_DIR:-/opt/blackstart}"
DATA_DIR="${DATA_DIR:-/var/lib/blackstart}"
ENV_FILE="${ENV_FILE:-/etc/blackstart.env}"

if [[ "$(id -u)" -ne 0 ]]; then
  echo "Run as root inside the LXC." >&2
  exit 1
fi

if [[ ! -f "$APP_DIR/backend/app/main.py" ]]; then
  echo "Blackstart is not at $APP_DIR. Clone it there first." >&2
  exit 1
fi

export DEBIAN_FRONTEND=noninteractive
apt-get update
apt-get install -y --no-install-recommends \
  python3 python3-venv python3-pip ca-certificates curl

id -u blackstart >/dev/null 2>&1 || useradd --system --home "$DATA_DIR" --shell /usr/sbin/nologin blackstart
mkdir -p "$DATA_DIR"
chown -R blackstart:blackstart "$DATA_DIR"
chown -R blackstart:blackstart "$APP_DIR"

if [[ ! -d "$APP_DIR/backend/.venv" ]]; then
  sudo -u blackstart python3 -m venv "$APP_DIR/backend/.venv"
fi
sudo -u blackstart "$APP_DIR/backend/.venv/bin/pip" install --upgrade pip
sudo -u blackstart "$APP_DIR/backend/.venv/bin/pip" install -r "$APP_DIR/backend/requirements.txt"

if [[ ! -f "$ENV_FILE" ]]; then
  SECRET="$(python3 -c 'import secrets; print(secrets.token_urlsafe(48))')"
  sed "s/^BLACKSTART_SECRET_KEY=.*/BLACKSTART_SECRET_KEY=${SECRET}/" \
    "$APP_DIR/deploy/blackstart.env.example" >"$ENV_FILE"
  chmod 600 "$ENV_FILE"
  echo "Wrote $ENV_FILE with a new secret key. Edit PEANUT_URL before first start if you want."
fi

install -m 644 "$APP_DIR/deploy/blackstart.service" /etc/systemd/system/blackstart.service
systemctl daemon-reload
systemctl enable --now blackstart.service

sleep 1
systemctl --no-pager --full status blackstart.service || true
echo
echo "Blackstart is listening on http://$(hostname -I | awk '{print $1}'):8088"
echo "Login: admin / blackstart   — change the password immediately."
