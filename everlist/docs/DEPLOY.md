# EverList Production Deployment

Target: any Ubuntu 22.04/24.04 VPS (RackNerd, Hetzner, Oracle). One script, idempotent — safe to re-run.

## Prerequisites

- **VPS**: Ubuntu 22.04+, 1 GB RAM is enough (hub is stdlib-only Python, ~200 MB)
- **Domain**: `everlist.network` — you control DNS
- **Repo**: `https://github.com/everlist-hq/everlist` pushed and current
- **Access**: root SSH

## 1. Order the VPS

| Provider | Price | Note |
| --- | --- | --- |
| RackNerd | ~$11–25/yr | cheapest permanent; upgrade = ticket, ~1 min reboot |
| Hetzner | ~€4.35/mo | reliability benchmark |
| Oracle Always Free | $0 | signup rejection roulette; **must also open ports 80/443 in the VCN security list** (iptables/ufw alone is NOT enough on Oracle) |

## 2. Run the deploy script (from any machine with the repo)

```bash
cat tools/deploy.sh | ssh root@YOUR_VPS_IP 'cat > /tmp/deploy.sh && chmod +x /tmp/deploy.sh && /tmp/deploy.sh'
```

The script does, in order:

1. Installs python3, git, ufw + **Caddy via the official apt repo** (arch-independent)
2. Writes `/etc/caddy/Caddyfile` — auto-HTTPS reverse proxy for `everlist.network`
3. Creates the `deploy` user and clones (or `git pull`s) the repo to `/home/deploy/everlist`
4. Generates **real production secrets** into `/home/deploy/everlist.env` (0600):
   `HUB_ADMIN_KEY`, `HUB_BOOKING_KEY` — re-runs keep the existing file
5. Installs the `everlist.service` systemd unit (auto-restart, runs as `deploy` from the repo root)
6. Configures the firewall: 22, 80 (ACME), 443

Environment used in production: `HUB_ENV=production`, `HUB_STORAGE_MODE=sqlite`, `HUB_PORT=8802` (behind Caddy).

## 3. Point DNS

`everlist.network` A record → VPS IP. Caddy obtains the certificate automatically once DNS resolves.

**Oracle only:** additionally open 80/443 in the VCN Security List (cloud console), or the outside world sees nothing.

## 4. Verify

```bash
curl -s https://everlist.network/.well-known/agent-hub.json   # hub descriptor
journalctl -u everlist -f                                     # hub logs
journalctl -u caddy -f                                        # TLS/proxy logs
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

## 6. Phase 2 — Agentverse wrapper (after hub is live)

The chat agent (`wrapper.py`) needs: `HUB_AGENT_SEED` (generate on VPS), `HUB_URL=http://localhost:8802`, and a venv with `uagents`/`httpx` + your Agentverse mailbox key. Run as a second systemd unit `everlist-wrapper`. Do this once the hub API is verified — one service at a time.

## Cost summary

- RackNerd 2.5 GB special: ~$20–25/**year**
- Domain: ~$10/year (owned)
- **Total: ~€30/year** — vs ~€52/year Hetzner, ~$100+/year big-cloud
