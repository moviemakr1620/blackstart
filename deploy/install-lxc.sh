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

as_blackstart() {
  # Debian CT images do not include sudo. runuser is from util-linux.
  if command -v runuser >/dev/null 2>&1; then
    runuser -u blackstart -- "$@"
  elif command -v sudo >/dev/null 2>&1; then
    sudo -u blackstart -- "$@"
  else
    su -s /bin/bash blackstart -c "$(printf '%q ' "$@")"
  fi
}

export DEBIAN_FRONTEND=noninteractive
apt-get update
apt-get install -y --no-install-recommends \
  python3 python3-venv python3-pip ca-certificates curl

id -u blackstart >/dev/null 2>&1 || useradd --system --home "$DATA_DIR" --shell /usr/sbin/nologin blackstart
mkdir -p "$DATA_DIR"
chown -R blackstart:blackstart "$DATA_DIR"
chown -R blackstart:blackstart "$APP_DIR"
# Root updates the tree with `git pull`; mark it safe so Git 2.35+ does not
# refuse a repo owned by the blackstart service user.
git config --global --add safe.directory "$APP_DIR"

if [[ ! -x "$APP_DIR/backend/.venv/bin/python" ]]; then
  as_blackstart python3 -m venv "$APP_DIR/backend/.venv"
fi
as_blackstart "$APP_DIR/backend/.venv/bin/pip" install --upgrade pip
as_blackstart "$APP_DIR/backend/.venv/bin/pip" install -r "$APP_DIR/backend/requirements.txt"

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
IP="$(hostname -I 2>/dev/null | awk '{print $1}')"
echo
echo "Blackstart should be on http://${IP:-<lxc-ip>}:8088"
echo "Login: admin / blackstart   — change the password immediately."
echo "Check:  systemctl status blackstart   and   curl -sS http://127.0.0.1:8088/api/health"
