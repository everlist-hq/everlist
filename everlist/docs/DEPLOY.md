# EverList Production Deployment

Target: any Ubuntu 22.04/24.04 VPS (cloudserver.net, Godlike, Hetzner, Oracle). One script, idempotent — safe to re-run.

## Prerequisites

- **VPS**: Ubuntu 22.04+, 2 GB RAM recommended (measured: hub + wrapper ≈ 274 MB)
- **Domain**: `everlist.network` — you control DNS
- **Repo**: `https://github.com/everlist-hq/everlist` pushed and current (verified public)
- **Access**: root SSH

## 1. Order the VPS

**Chosen & ordered 2026-09-12:** cloudserver.net LowEndBox special — 2 GB / 1 vCPU / 50 GB SSD / 5 TB / Los Angeles / **Ubuntu 24.04** — $23.88/**year**, renews at the same price, paid via PayPal.

Vetted alternatives (2026 prices, for migration/fallback):

| Provider | Price | Note |
| --- | --- | --- |
| Godlike Nitro 1 | €3.49/mo (€1.74 first month) | 2 GB KVM, monthly cancel-anytime; fallback if provisioning fails |
| Hetzner CAX11 | €7.79/mo | post-June-2026 price hike; was out of stock at evaluation |
| Oracle Always Free | $0 | signup roulette; **must also open 80/443 in the VCN security list** (ufw alone is NOT enough on Oracle) |

Provisioning at cloudserver.net can take 1–2 days (their known weak spot). PayPal dispute covers non-delivery.

## 2. Run the deploy script (from any machine with the repo)

```bash
cat tools/deploy.sh | ssh root@YOUR_VPS_IP 'cat > /tmp/deploy.sh && chmod +x /tmp/deploy.sh && /tmp/deploy.sh'
```

The script does, in order:

1. Installs python3, git, ufw + **Caddy via the official apt repo** (arch-independent)
2. Writes `/etc/caddy/Caddyfile` — auto-HTTPS reverse proxy for `everlist.network` (hub) and `chat.everlist.network` (web chat UI)
3. Creates the `deploy` user and clones (or `git pull`s) the repo to `/home/deploy/everlist`
4. Generates **real production secrets** into `/home/deploy/everlist.env` (0600):
   `HUB_ADMIN_KEY`, `HUB_BOOKING_KEY`, `HUB_AGENT_SEED` — re-runs keep the existing file
5. Installs `everlist.service` + `everlist-wrapper.service` + `everlist-webchat.service` systemd units (auto-restart, run as `deploy`)
6. Creates the wrapper venv (`uagents`, `httpx`)
7. Configures the firewall: 22, 80 (ACME), 443

Environment used in production: `HUB_ENV=production`, `HUB_STORAGE_MODE=sqlite`, `HUB_PORT=8802` (behind Caddy).

## 3. Point DNS

`everlist.network` A record → VPS IP. Caddy obtains the certificate automatically once DNS resolves.

**Chat subdomain:** add a `chat` A record → the same VPS IP (or a `chat.everlist.network` CNAME → `everlist.network`). The deploy script's Caddyfile already serves `chat.<domain>` → webchat on 127.0.0.1:8804.

**Oracle only:** additionally open 80/443 in the VCN Security List (cloud console), or the outside world sees nothing.

## 4. Verify

```bash
curl -s https://everlist.network/.well-known/agent-hub.json          # hub live
curl -s https://chat.everlist.network/api/health                    # web chat live
journalctl -u everlist -f                                           # hub logs
journalctl -u everlist-wrapper -f                                   # chat agent logs
journalctl -u everlist-webchat -f                                   # web chat logs
journalctl -u caddy -f                                              # TLS/proxy logs
```

Smoke checks: `GET /search` (read), one demo listing via `tools/seed_demo.py --hub https://everlist.network` if desired.

## 5. Operations cheat-sheet

| Task | Command |
| --- | --- |
| Deploy update | re-run the script (pulls, preserves state) |
| Hub logs | `journalctl -u everlist -f` |
| Restart hub | `systemctl restart everlist` |
| Rotate admin key | edit `/home/deploy/everlist.env` → `systemctl restart everlist` |
| Backups | built-in `backups/` rotation (H17) + `tools/restore_backup.py` |
| Self-check | `python3 tools/selfcheck.py --hub http://localhost:8802` |
| Migrate host | clone repo on new box → run script → restore state → repoint DNS (~30 min) |

## 6. Phase 2 — Agentverse wrapper (after hub is live)

The chat agent (`wrapper.py`) runs as the second systemd unit and needs `HUB_AGENT_SEED` (auto-generated on the VPS), `HUB_URL=http://127.0.0.1:8802`, and the venv the script creates. Activate the Agentverse mailbox with your Agentverse key once the hub API is verified — one service at a time.

## Cost summary

- cloudserver.net 2 GB: $23.88/**year** (renews same price)
- Domain: ~$10/year (owned)
- **Total: ~€32/year**
