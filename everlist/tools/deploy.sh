#!/bin/bash
# EverList production deployment — Ubuntu 22.04/24.04, run as root.
# Idempotent: safe to re-run; existing state.json is preserved.
set -euo pipefail

DOMAIN="${DOMAIN:-everlist.network}"
REPO_URL="${REPO_URL:-https://github.com/everlist-hq/everlist.git}"
HUB_PORT="${HUB_PORT:-8802}"
WEBCHAT_PORT="${WEBCHAT_PORT:-8804}"
INSTALL_DIR=/home/deploy/everlist
ENVF=/home/deploy/everlist.env

[ "$(id -u)" -eq 0 ] || { echo "ERROR: run as root"; exit 1; }
echo "=== EverList deployment: $DOMAIN -> :$HUB_PORT ==="

# 1. system deps (incl. Caddy apt repo prerequisites)
apt-get update
DEBIAN_FRONTEND=noninteractive apt-get install -y \
  python3-pip python3-venv git curl ufw debian-keyring debian-archive-keyring \
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
{
    email admin@$DOMAIN
    acme_ca https://acme-v02.api.letsencrypt.org/directory
}

$DOMAIN {
    encode zstd gzip

    # Browser chat is the face of the site; the agent hub API shares the same
    # domain under its own paths (agents discover it via /.well-known/...).
    @chat path / /index.html /api/* /app.js /style.css /favicon.svg /nacl-fast.min.js /l/* /booking/* /transparency /network /how /agents /og/* /theme.js /sw.js /manifest.webmanifest /logo-512.png /sitemap.xml /robots.txt
    handle @chat {
        reverse_proxy 127.0.0.1:$WEBCHAT_PORT
    }
    handle {
        reverse_proxy 127.0.0.1:$HUB_PORT
    }
}

# Chat subdomain is a friendly alias -> canonical apex
chat.$DOMAIN {
    redir https://$DOMAIN{uri} 301
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

# Resolve app root: the public repo nests the hub under everlist/; support both layouts
if [ -f "$INSTALL_DIR/app.py" ]; then
  APP_DIR="$INSTALL_DIR"
elif [ -f "$INSTALL_DIR/everlist/app.py" ]; then
  APP_DIR="$INSTALL_DIR/everlist"
else
  echo "ERROR: app.py not found after clone/pull under $INSTALL_DIR" >&2
  exit 1
fi
echo "[layout] app root: $APP_DIR"

# 5. production env file — real admin/booking/seed keys generated HERE (0600).
if [ ! -s "$ENVF" ]; then
  umask 077
  {
    echo "HUB_PORT=$HUB_PORT"
    echo "HUB_ENV=production"
    echo "HUB_STORAGE_MODE=sqlite"
    echo "HUB_ADMIN_KEY=$(openssl rand -hex 32)"
    echo "HUB_BOOKING_KEY=$(openssl rand -hex 32)"
    echo "HUB_AGENT_SEED=$(openssl rand -hex 32)"
  } > "$ENVF"
  echo "[secrets] generated $ENVF (admin + booking + agent seed)"
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
WorkingDirectory=$APP_DIR
EnvironmentFile=$ENVF
ExecStart=/usr/bin/python3 -u $APP_DIR/app.py
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
EOF

# 7. wrapper unit (agentverse chat agent, depends on hub)
cat > /etc/systemd/system/everlist-wrapper.service <<WRAPPER
[Unit]
Description=EverList Agentverse Wrapper
After=everlist.service
Requires=everlist.service

[Service]
Type=simple
User=deploy
WorkingDirectory=$APP_DIR
EnvironmentFile=$ENVF
Environment="HUB_URL=http://127.0.0.1:$HUB_PORT"
ExecStart=$INSTALL_DIR/venv/bin/python $APP_DIR/wrapper.py
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
WRAPPER

cat > /etc/systemd/system/everlist-webchat.service <<WEBCHAT
[Unit]
Description=EverList Web Chat (chat-first web UI)
After=everlist.service
Requires=everlist.service

[Service]
Type=simple
User=deploy
WorkingDirectory=$APP_DIR
EnvironmentFile=$ENVF
Environment="WEBCHAT_HUB_URL=http://127.0.0.1:$HUB_PORT"
ExecStart=$INSTALL_DIR/venv/bin/python $APP_DIR/webchat.py
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
WEBCHAT

# 8. install wrapper dependencies (idempotent; venv is required by wrapper ExecStart)
cd "$INSTALL_DIR"
[ -x venv/bin/python ] || python3 -m venv venv
source venv/bin/activate
pip install --quiet --upgrade pip
pip install --quiet -r "$APP_DIR/requirements.txt" || echo "[warn] venv requirements install failed"

# 9. permissions
chown -R deploy:deploy "$INSTALL_DIR"
chown deploy:deploy "$ENVF"
chmod 600 "$ENVF"
systemctl daemon-reload
systemctl enable everlist everlist-wrapper everlist-webchat
systemctl restart everlist everlist-wrapper everlist-webchat

# 10. firewall
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
echo "2) Verify hub:  curl -s https://$DOMAIN/.well-known/agent-hub.json"
echo "3) Verify wrapper: curl -s https://$DOMAIN/wrapper/health"
echo "4) Verify chat: curl -s https://chat.$DOMAIN/api/health"
echo "5) Logs:    journalctl -u everlist -f"
