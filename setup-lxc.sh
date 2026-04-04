#!/usr/bin/env bash
#
# CarSearch LXC Setup for Proxmox
#
# Run this on your Proxmox host:
#   bash <(curl -sL https://raw.githubusercontent.com/mattcree/ni-car-search/main/setup-lxc.sh)
#
# To update later:
#   pct exec 200 -- carsearch-update
#

set -euo pipefail

# ── Configuration ───────────────────────────────────────────────────────────
CTID="${CTID:-200}"
HOSTNAME="carsearch"
STORAGE="${STORAGE:-local-lvm}"
TEMPLATE_STORAGE="${TEMPLATE_STORAGE:-local}"
PASSWORD="${PASSWORD:-carsearch}"
MEMORY=2048
SWAP=512
DISK=8
CORES=2
BRIDGE="vmbr0"
INNER_SCRIPT="https://raw.githubusercontent.com/mattcree/ni-car-search/main/setup-inside-lxc.sh"

echo "=== CarSearch LXC Setup ==="
echo "  Container ID: $CTID"
echo "  Storage: $STORAGE"
echo ""

# ── Download template if needed ─────────────────────────────────────────────
if ! pveam list "$TEMPLATE_STORAGE" | grep -q "debian-12"; then
    echo "Downloading Debian 12 template..."
    pveam update
    TEMPLATE=$(pveam available --section system | grep "debian-12-standard" | tail -1 | awk '{print $2}')
    if [ -z "$TEMPLATE" ]; then
        echo "ERROR: No Debian 12 template found."
        exit 1
    fi
    echo "  Using template: $TEMPLATE"
    pveam download "$TEMPLATE_STORAGE" "$TEMPLATE"
fi
TEMPLATE_PATH=$(pveam list "$TEMPLATE_STORAGE" | grep "debian-12" | head -1 | awk '{print $1}')
if [ -z "$TEMPLATE_PATH" ]; then
    echo "ERROR: Template not found."
    exit 1
fi

# ── Create container ────────────────────────────────────────────────────────
echo "Creating LXC container $CTID..."
pct create "$CTID" "$TEMPLATE_PATH" \
    --password "$PASSWORD" \
    --hostname "$HOSTNAME" \
    --storage "$STORAGE" \
    --rootfs "$STORAGE:$DISK" \
    --memory "$MEMORY" \
    --swap "$SWAP" \
    --cores "$CORES" \
    --net0 "name=eth0,bridge=$BRIDGE,ip=dhcp" \
    --features "nesting=1" \
    --unprivileged 1 \
    --start 0

# ── Start container ─────────────────────────────────────────────────────────
echo "Starting container..."
pct start "$CTID"
sleep 5

# ── Install curl inside, then download and run the inner setup script ───────
echo "Installing inside container..."
pct exec "$CTID" -- apt-get update -qq
pct exec "$CTID" -- apt-get install -y -qq curl
pct exec "$CTID" -- bash -c "curl -sL $INNER_SCRIPT | PORT=${PORT:-8000} bash"

# ── Done ────────────────────────────────────────────────────────────────────
IP=$(pct exec "$CTID" -- hostname -I | awk '{print $1}')
echo ""
echo "=== CarSearch is running ==="
echo "  URL:      http://${IP}:${PORT:-8000}"
echo "  SSH:      ssh root@${IP}  (password: ${PASSWORD})"
echo "  LXC:      pct enter $CTID"
echo "  Logs:     pct exec $CTID -- journalctl -u carsearch -f"
echo "  Update:   pct exec $CTID -- carsearch-update"
echo ""
