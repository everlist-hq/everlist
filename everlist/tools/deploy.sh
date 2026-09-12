#!/bin/bash
set -e

echo "=== EverList VPS Deployment ==="

# 1. Update system & install dependencies
echo "[1/5] Installing dependencies..."
apt-get update && apt-get install -y python3-pip python3-venv git curl wget

# 2. Install Caddy (Auto-HTTPS)
echo "[2/5] Installing Caddy..."
curl -1sS https://download.caddyserver.com/caddy-2.8.4-linux-amd64.tar.gz | tar xz
cp caddy /usr/local/bin/
cp /etc/systemd/system/caddy.service /etc/systemd/system/
systemctl daemon-reload
systemctl enable caddy

# 3. Clone Staging Repo
echo "[3/5] Cloning staging repo..."
cd /home/deploy
mkdir -p everlist
cd everlist
git clone https://github.com/everlist-hq/everlist.git .

# 4. Setup Python Environment
echo "[4/5] Setting up Python env..."
python3 -m venv venv
source venv/bin/activate
pip install flask requests cryptography

# 5. Create Systemd Service
echo "[5/5] Creating systemd service..."
cat > /etc/systemd/system/everlist.service <<EOF
[Unit]
Description=EverList Hub
After=network.target

[Service]
Type=simple
User=deploy
WorkingDirectory=/home/deploy/everlist/experiments/agent-hub-v2
Environment="HUB_PORT=8802"
ExecStart=/home/deploy/everlist/venv/bin/python app.py
Restart=always

[Install]
WantedBy=multi-user.target
EOF

systemctl daemon-reload
systemctl enable everlist

echo "Deployment complete. Set up DNS now."