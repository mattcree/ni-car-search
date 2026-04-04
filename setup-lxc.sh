#!/usr/bin/env bash
#
# CarSearch LXC Setup for Proxmox
#
# Run this on your Proxmox host:
#   bash <(curl -sL https://raw.githubusercontent.com/mattcree/ni-car-search/main/setup-lxc.sh)
#
# Or copy-paste the whole thing into a shell.
#
# What it does:
#   1. Creates a Debian 12 LXC container
#   2. Installs Python, Chrome, git
#   3. Clones the repo
#   4. Sets up a systemd service on port 8000
#   5. Starts it
#
# To update later, SSH into the container and run:
#   carsearch-update
#

set -euo pipefail

# ── Configuration ───────────────────────────────────────────────────────────
CTID="${CTID:-200}"                      # Container ID (change if 200 is taken)
HOSTNAME="carsearch"
STORAGE="${STORAGE:-local-lvm}"          # Proxmox storage (local-lvm, local-zfs, etc.)
TEMPLATE_STORAGE="${TEMPLATE_STORAGE:-local}"  # Where templates are stored
MEMORY=2048                              # MB
SWAP=512
DISK=8                                   # GB
CORES=2
BRIDGE="vmbr0"
REPO="https://github.com/mattcree/ni-car-search.git"
PORT=8000

echo "=== CarSearch LXC Setup ==="
echo "  Container ID: $CTID"
echo "  Storage: $STORAGE"
echo "  Template storage: $TEMPLATE_STORAGE"
echo ""

# ── Download template if needed ─────────────────────────────────────────────
TEMPLATE="debian-12-standard_12.7-1_amd64.tar.zst"
if ! pveam list "$TEMPLATE_STORAGE" | grep -q "debian-12"; then
    echo "Downloading Debian 12 template..."
    pveam update
    pveam download "$TEMPLATE_STORAGE" "$TEMPLATE"
fi
TEMPLATE_PATH=$(pveam list "$TEMPLATE_STORAGE" | grep "debian-12" | head -1 | awk '{print $1}')

# ── Create container ────────────────────────────────────────────────────────
echo "Creating LXC container $CTID..."
pct create "$CTID" "$TEMPLATE_PATH" \
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

# ── Start and configure ────────────────────────────────────────────────────
echo "Starting container..."
pct start "$CTID"
sleep 5  # Wait for network

echo "Installing dependencies inside container..."
pct exec "$CTID" -- bash -c '
set -euo pipefail

export DEBIAN_FRONTEND=noninteractive

# System packages
apt-get update -qq
apt-get install -y -qq \
    python3 python3-pip python3-venv \
    git wget gnupg2 curl \
    > /dev/null 2>&1

# Google Chrome (for Playwright)
wget -q -O - https://dl.google.com/linux/linux_signing_key.pub | gpg --dearmor -o /usr/share/keyrings/google.gpg
echo "deb [arch=amd64 signed-by=/usr/share/keyrings/google.gpg] http://dl.google.com/linux/chrome/deb/ stable main" \
    > /etc/apt/sources.list.d/google-chrome.list
apt-get update -qq
apt-get install -y -qq google-chrome-stable > /dev/null 2>&1

# Clone repo
git clone '"$REPO"' /opt/carsearch
cd /opt/carsearch

# Python venv + deps
python3 -m venv /opt/carsearch/venv
/opt/carsearch/venv/bin/pip install --quiet -e .
/opt/carsearch/venv/bin/playwright install chromium

# Data directory
mkdir -p /root/.carsearch

# ── Systemd service ─────────────────────────────────────────────────────
cat > /etc/systemd/system/carsearch.service << UNIT
[Unit]
Description=CarSearch Web App
After=network.target

[Service]
Type=simple
WorkingDirectory=/opt/carsearch
ExecStart=/opt/carsearch/venv/bin/python -m web
Restart=always
RestartSec=5
Environment=CARSEARCH_HOST=0.0.0.0
Environment=CARSEARCH_PORT='"$PORT"'

[Install]
WantedBy=multi-user.target
UNIT

systemctl daemon-reload
systemctl enable carsearch
systemctl start carsearch

# ── Update script ───────────────────────────────────────────────────────
cat > /usr/local/bin/carsearch-update << UPDATE
#!/usr/bin/env bash
set -euo pipefail
echo "Updating CarSearch..."
cd /opt/carsearch
git pull --ff-only
/opt/carsearch/venv/bin/pip install --quiet -e .
systemctl restart carsearch
echo "Done. Service restarted."
systemctl status carsearch --no-pager
UPDATE
chmod +x /usr/local/bin/carsearch-update
'

# ── Done ────────────────────────────────────────────────────────────────────
IP=$(pct exec "$CTID" -- hostname -I | awk '{print $1}')
echo ""
echo "=== CarSearch is running ==="
echo "  URL:    http://${IP}:${PORT}"
echo "  LXC:    pct enter $CTID"
echo "  Logs:   pct exec $CTID -- journalctl -u carsearch -f"
echo "  Update: pct exec $CTID -- carsearch-update"
echo ""
