#!/bin/sh
# Start Blackstart as the unprivileged "blackstart" user.
# Runs as root only long enough to fix /data ownership (volumes created by
# older root-run images) and to join the Docker socket's group if one is mounted.
set -eu
umask 0077

DATA="${BLACKSTART_DATA_DIR:-/data}"

if [ "$(id -u)" != "0" ]; then
  exec "$@"
fi

if [ "${BLACKSTART_RUN_AS_ROOT:-false}" = "true" ]; then
  echo "blackstart: BLACKSTART_RUN_AS_ROOT=true, staying root (not recommended)" >&2
  exec "$@"
fi

mkdir -p "$DATA"
chown -R blackstart:blackstart "$DATA"

GROUPS_ARG="--clear-groups"
if [ -S /var/run/docker.sock ]; then
  SOCK_GID="$(stat -c %g /var/run/docker.sock)"
  GROUPS_ARG="--groups=${SOCK_GID}"
  echo "blackstart: Docker socket mounted; joining its group (gid ${SOCK_GID})" >&2
fi

exec setpriv --reuid=blackstart --regid=blackstart ${GROUPS_ARG} --inh-caps=-all --no-new-privs -- "$@"
