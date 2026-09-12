#!/bin/bash
set -e

echo "=== EverList VPS Deployment ==="

# Detect architecture (Oracle Always Free = ARM64, Hetzner CAX = ARM, CX = AMD)
ARCH=$(uname -m)
if [ "$ARCH" = "aarch64" ]; then
    CADDY_URL="https://download.caddyserver.com/caddy-2.8.4-linux-arm64.tar.gz"
    echo "[INFO] Detected ARM64 (Oracle/Hetzner ARM). Using ARM Caddy."
elif [ "$ARCH" = "x86_64" ]; then
    CADDY_URL="https://download.caddyserver.com/caddy-2.8.4-linux-amd64.tar.gz"
    echo "[INFO] Detected AMD64 (Hetzner AMD). Using AMD Caddy."
else
    echo "[ERROR] Unsupported architecture: $ARCH"
    exit 1
fi

# 1. Update system & install dependencies
echo "[1/5] Installing dependencies..."
apt-get update
apt-get install -y python3-pip python3-venv git curl wget ufw

# 2. Install Caddy (Auto-HTTPS)
echo "[2/5] Installing Caddy ($ARCH)..."
curl -1sS "$CADDY_URL" | tar xz
cp caddy /usr/local/bin/
cp /etc/systemd/system/caddy.service /etc/systemd/system/
systemctl daemon-reload
systemctl enable caddy

# 3. Setup deploy user & clone repo
echo "[3/5] Setting up deploy user..."
if ! id deploy &>/dev/null; then
    useradd -m -s /bin/bash deploy
fi
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

# 6. Firewall setup
echo "[CONFIG] Configure firewall..."
ufw allow 22/tcp
ufw allow 443/tcp
ufw enable --force

echo "Deployment complete."
echo "Next steps:"
echo "1. Generate secrets: openssl rand -hex 32 > /home/deploy/everlist/experiments/agent-hub-v2/.secrets/agent_seed"
echo "2. Set DNS: Point everlist.network A record to this VPS IP"
echo "3. Verify: curl -s https://everlist.network/health"
