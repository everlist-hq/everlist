# EverList Production Deployment (RackNerd Edition)

## Prerequisites

- **VPS**: RackNerd (~$11/year). Choose 1 vCPU/1GB RAM minimum. (ARM or AMD is fine; deploy.sh detects automatically).
- **Domain**: `everlist.network` (already owned).
- **Access**: Root SSH access to the VPS.

## 1. Prepare Repository Access

If `https://github.com/everlist-hq/everlist` is private, you must configure SSH on the VPS:

```bash
ssh root@your-vps-ip
mkdir -p ~/.ssh
cd ~/.ssh
cat >> config <<CONF
Host github.com
    AddKeysToAgent yes
    IdentitiesOnly yes
    IdentityFile ~/.ssh/id_rsa
CONF

# Generate key pair if you don't have one
ssh-keygen -t ed25519 -C "everlist@vps"

# Add public key to GitHub: https://github.com/settings/keys
cat ~/.ssh/id_rsa.pub
```

## 2. Run Deployment Script

Copy the script from the repo root to the VPS and run as root:

```bash
# On your local machine
cat tools/deploy.sh | ssh root@your-vps-ip 'cat > /tmp/deploy.sh && chmod +x /tmp/deploy.sh && /tmp/deploy.sh'
```

**Note:** The script detects ARM/AMD, installs Caddy, configures systemd, and sets up the firewall.

## 3. Generate Secrets

Create the secrets file immediately after deployment:

```bash
mkdir -p /home/deploy/everlist/experiments/agent-hub-v2/.secrets
openssl rand -hex 32 > /home/deploy/everlist/experiments/agent-hub-v2/.secrets/agent_seed
chmod 600 /home/deploy/everlist/experiments/agent-hub-v2/.secrets/agent_seed
```

## 4. DNS Setup

Point `everlist.network` A record to the VPS IP.

## 5. Verify

```bash
curl -s https://everlist.network/health
systemctl status everlist
```

## 6. Agentverse Mailbox

Update the EverList Booking agent to use the new URL (requires Agentverse access).

## Cost Estimate

- **VPS**: ~$11.29/year (RackNerd).
- **Domain**: ~$10/year (your domain).
- **Total**: ~$21/year.
