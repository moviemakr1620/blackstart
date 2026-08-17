#!/usr/bin/env bash
# Run on the PROXMOX HOST (not inside a guest) to create the Blackstart LXC.
# Edit the variables below first.
set -euo pipefail

CTID="${CTID:-120}"
HOSTNAME="${HOSTNAME:-blackstart}"
# Storage for the rootfs. Check: pvesm status
STORAGE="${STORAGE:-local-lvm}"
# Template storage + file. Check: ls /var/lib/vz/template/cache
TEMPLATE="${TEMPLATE:-local:vztmpl/debian-12-standard_12.12-1_amd64.tar.zst}"
DISK_GB="${DISK_GB:-8}"
MEMORY_MB="${MEMORY_MB:-1024}"
CORES="${CORES:-2}"
BRIDGE="${BRIDGE:-vmbr0}"
# dhcp  or  192.168.1.55/24,gw=192.168.1.1
IP="${IP:-dhcp}"
UNPRIVILEGED="${UNPRIVILEGED:-1}"

if [[ "$(id -u)" -ne 0 ]]; then
  echo "Run as root on the Proxmox host." >&2
  exit 1
fi

if pct status "$CTID" >/dev/null 2>&1; then
  echo "CT $CTID already exists." >&2
  exit 1
fi

# Download the Debian template if this exact name is missing.
if ! pveam list local | grep -q "$(basename "${TEMPLATE#*:}")"; then
  echo "Template not cached. Available Debian 12 images:"
  pveam update >/dev/null
  pveam available --section system | grep debian-12 || true
  echo
  echo "Download one, then set TEMPLATE= in this script. Example:"
  echo "  pveam download local debian-12-standard_12.12-1_amd64.tar.zst"
  exit 1
fi

NET="name=eth0,bridge=${BRIDGE},ip=${IP}"
if [[ "$IP" == "dhcp" ]]; then
  NET="name=eth0,bridge=${BRIDGE},ip=dhcp"
fi

pct create "$CTID" "$TEMPLATE" \
  --hostname "$HOSTNAME" \
  --memory "$MEMORY_MB" \
  --cores "$CORES" \
  --rootfs "${STORAGE}:${DISK_GB}" \
  --net0 "$NET" \
  --unprivileged "$UNPRIVILEGED" \
  --features nesting=1 \
  --onboot 1 \
  --ostype debian \
  --start 1

echo
echo "Created CT $CTID ($HOSTNAME)."
echo "Get a shell:   pct enter $CTID"
echo "Then follow deploy/lxc.md from the 'Inside the LXC' section."
