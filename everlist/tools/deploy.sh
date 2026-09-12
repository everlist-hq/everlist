#!/bin/bash
# EverList production deployment — Ubuntu 22.04/24.04, run as root.
# Idempotent: safe to re-run; existing state.json is preserved.
set -euo pipefail

DOMAIN="${DOMAIN:-everlist.network}"
REPO_URL="${REPO_URL:-https://github.com/everlist-hq/everlist.git}"
HUB_PORT="${HUB_PORT:-8802}"
INSTALL_DIR=/home/deploy/everlist
ENVF=/home/deploy/everlist.env

[ "$(id -u)" -eq 0 ] || { echo "ERROR: run as root"; exit 1; }
echo "=== EverList deployment: $DOMAIN -> :$HUB_PORT ==="

# 1. system deps (incl. Caddy apt repo prerequisites)
apt-get update
DEBIAN_FRONTEND=noninteractive apt-get install -y \
  python3 git curl ufw debian-keyring debian-archive-keyring \
  apt-transport-https gnupg

# 2. Caddy via official apt repo (arch-independent, ships its own systemd unit)
curl -1sLf 'https://dl.cloudsmith.io/public/caddy/stable/gpg.key' \
  | gpg --dearmor --yes -o /usr/share/keyrings/caddy-stable-archive-keyring.gpg
curl -1sLf 'https://dl.cloudsmith.io/public/caddy/stable/debian.deb.txt' \
  | tee /etc/apt/sources.list.d/caddy-stable.list >/dev/null
apt-get update
DEBIAN_FRONTEND=noninteractive apt-get install -y caddy

# 3. Caddyfile: auto-HTTPS reverse proxy to the hub
mkdir -p /etc/caddy
cat > /etc/caddy/Caddyfile <<EOF
$DOMAIN {
encode zstd gzip
reverse_proxy 127.0.0.1:$HUB_PORT
}
EOF
systemctl enable caddy
systemctl restart caddy

# 4. deploy user + repo (pull if already cloned — preserves state.json)
id -u deploy &>/dev/null || useradd -m -s /bin/bash deploy
if [ -d "$INSTALL_DIR/.git" ]; then
  git -C "$INSTALL_DIR" pull --ff-only || echo "[warn] git pull failed; keeping existing tree"
else
  git clone "$REPO_URL" "$INSTALL_DIR"
fi

# 5. production env file — real admin/booking keys generated HERE (0600).
# The hub is stdlib-only python; no agent_seed is needed for the hub itself.
if [ ! -s "$ENVF" ]; then
  umask 077
  {
    echo "HUB_PORT=$HUB_PORT"
    echo "HUB_ENV=production"
    echo "HUB_STORAGE_MODE=sqlite"
    echo "HUB_ADMIN_KEY=$(openssl rand -hex 32)"
    echo "HUB_BOOKING_KEY=$(openssl rand -hex 32)"
  } > "$ENVF"
  echo "[secrets] generated $ENVF (admin + booking keys)"
else
  echo "[secrets] keeping existing $ENVF"
fi

# 6. systemd unit (hub runs from repo root, as deploy user)
cat > /etc/systemd/system/everlist.service <<EOF
[Unit]
Description=EverList Hub
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=deploy
WorkingDirectory=$INSTALL_DIR
EnvironmentFile=$ENVF
ExecStart=/usr/bin/python3 -u $INSTALL_DIR/app.py
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
EOF

chown -R deploy:deploy "$INSTALL_DIR"
chown deploy:deploy "$ENVF"
chmod 600 "$ENVF"
systemctl daemon-reload
systemctl enable everlist
systemctl restart everlist

# 7. firewall (22 first so SSH survives)
ufw allow 22/tcp
ufw allow 80/tcp   # ACME http-01
ufw allow 443/tcp
ufw --force enable

echo "=== deployment complete ==="
sleep 2
systemctl --no-pager -l status everlist | head -6 || true
echo
echo "1) Point DNS A record of $DOMAIN at this server's IP"
echo "   (Oracle ONLY: also open 80/443 in the VCN security list!)"
echo "2) Verify:  curl -s https://$DOMAIN/.well-known/agent-hub.json"
echo "3) Logs:    journalctl -u everlist -f"
