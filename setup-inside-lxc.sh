#!/usr/bin/env bash
set -euo pipefail

PORT="${PORT:-8000}"

echo "=== Installing Chrome ==="
wget -q -O /tmp/google.pub https://dl.google.com/linux/linux_signing_key.pub
gpg --dearmor -o /usr/share/keyrings/google.gpg /tmp/google.pub
printf 'deb [arch=amd64 signed-by=/usr/share/keyrings/google.gpg] http://dl.google.com/linux/chrome/deb/ stable main\n' > /etc/apt/sources.list.d/google-chrome.list
apt-get update
apt-get install -y google-chrome-stable

echo "=== Cloning repo ==="
git clone https://github.com/mattcree/ni-car-search.git /opt/carsearch

echo "=== Setting up Python ==="
cd /opt/carsearch
python3 -m venv venv
venv/bin/pip install -e .
venv/bin/playwright install chromium
mkdir -p /root/.carsearch

echo "=== Creating systemd service ==="
cat > /etc/systemd/system/carsearch.service << 'UNIT'
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
Environment=CARSEARCH_PORT=${PORT}

[Install]
WantedBy=multi-user.target
UNIT

systemctl daemon-reload
systemctl enable carsearch
systemctl start carsearch

echo "=== Creating update script ==="
cat > /usr/local/bin/carsearch-update << 'UPDATE'
#!/usr/bin/env bash
set -euo pipefail
echo "Updating CarSearch..."
cd /opt/carsearch
git pull --ff-only
/opt/carsearch/venv/bin/pip install --quiet -e .
systemctl restart carsearch
echo "Done."
systemctl status carsearch --no-pager
UPDATE
chmod +x /usr/local/bin/carsearch-update

echo ""
echo "=== Done ==="
IP=$(hostname -I | awk '{print $1}')
echo "  URL: http://${IP}:${PORT}"
