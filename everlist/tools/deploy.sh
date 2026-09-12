#!/bin/bash
set -e

echo "=== EverList VPS Deployment ==="

# Detect architecture
ARCH=$(uname -m)
if [ "$ARCH" = "aarch64" ]; then
    CADDY_URL="https://download.caddyserver.com/caddy-2.8.4-linux-arm64.tar.gz"
elif [ "$ARCH" = "x86_64" ]; then
    CADDY_URL="https://download.caddyserver.com/caddy-2.8.4-linux-amd64.tar.gz"
else
    echo "[ERROR] Unsupported architecture: $ARCH"
    exit 1
fi

# 1. Update & install dependencies
echo "[1/6] Installing dependencies..."
apt-get update
apt-get install -y python3-pip python3-venv git curl wget ufw

# 2. Install Caddy (Auto-HTTPS)
echo "[2/6] Installing Caddy..."
curl -1sS "$CADDY_URL" | tar xz
cp caddy /usr/local/bin/
cp /etc/systemd/system/caddy.service /etc/systemd/system/
systemctl daemon-reload
systemctl enable caddy

# 3. Setup deploy user & clone repo
echo "[3/6] Setting up deploy user..."
if ! id deploy &>/dev/null; then
    useradd -m -s /bin/bash deploy
fi
cd /home/deploy
mkdir -p everlist
cd everlist
git clone https://github.com/everlist-hq/everlist.git .

# 4. Setup Python Environment
echo "[4/6] Setting up Python env..."
python3 -m venv venv
source venv/bin/activate

# App is stdlib-only, but cryptography needed for signing
pip install cryptography

# 5. Create Systemd Service
echo "[5/6] Creating systemd service..."
cat > /etc/systemd/system/everlist.service <<'SERVICE'
[Unit]
Description=EverList Hub
After=network.target

[Service]
Type=simple
User=deploy
WorkingDirectory=/home/deploy/everlist/experiments/agent-hub-v2
Environment="HUB_PORT=8802"
Environment="HUB_STORAGE_MODE=file"
Environment="HUB_ENV=production"
ExecStart=/home/deploy/everlist/venv/bin/python app.py
Restart=always

[Install]
WantedBy=multi-user.target
SERVICE

systemctl daemon-reload
systemctl enable everlist

# 6. Firewall setup
echo "[6/6] Configuring firewall..."
ufw allow 22/tcp
ufw allow 80/tcp    # ACME certificate issuance
ufw allow 443/tcp   # HTTPS
ufw enable --force

echo "=== Deployment complete ==="
echo ""
echo "Next steps:"
echo "1. Generate secrets on VPS:"
echo "   mkdir -p /home/deploy/everlist/experiments/agent-hub-v2/.secrets"
echo "   openssl rand -hex 32 > /home/deploy/everlist/experiments/agent-hub-v2/.secrets/agent_seed"
echo "   chmod 600 /home/deploy/everlist/experiments/agent-hub-v2/.secrets/agent_seed"
echo ""
echo "2. Set DNS: Point everlist.network A record to this VPS IP"
echo ""
echo "3. Verify:"
echo "   curl -s https://everlist.network/health"
echo "   systemctl status everlist"
