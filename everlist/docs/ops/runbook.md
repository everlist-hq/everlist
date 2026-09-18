# EverList Ops Runbook (2026-09-18)

Owner-approved resilience batch: systemd hardening, self-heal + external
watchdogs, weekly organizer digest, nightly encrypted backups (2-machine),
restore-drilled.

## The four layers

| Layer | Where | What | Schedule |
| --- | --- | --- | --- |
| systemd Restart=always | VPS | service crash recovery | instant |
| on-server watchdog | VPS `/usr/local/sbin/everlist-watchdog.sh` | checks hub+webchat+wrapper locally, restarts on hang, emails owner (rate-limited 1/h) | every 5 min (cron) |
| **external watchdog** | A0 container `/a0/usr/projects/everlist/ops/external-watchdog.sh` | checks https://everlist.network from OUTSIDE (catches Caddy/DNS/network/whole-server down), emails down + RECOVERED alerts | every 10 min (container cron) |
| backups | VPS `/root/backups/` + container `backups-offsite/` | consistent SQLite snapshot + env, GPG-encrypted, 14 kept | VPS 03:17 UTC; container pull 03:40 UTC |

## Alert path

Both watchdogs email via the hub's Resend SMTP (free tier). Recipient:
`WATCHDOG_EMAIL` in the box env (also `.secrets/alert-smtp.env` container-side).
Test anytime:
- external: `ops/external-watchdog.sh --test-mail`
- on-server: see runbook history / worklog 2026-09-18

## Weekly organizer digest

Mondays 09:00 UTC the box calls `POST /admin/digest/send` (admin-gated).
One branded summary per organizer (verified email + notify on) with the
week's created/confirmed/cancelled counts + gross. Zero-state mail is honest
("a quiet week"). Dry-run: `{"dry_run": true}` sends nothing.

## Backup & restore

- Key: GPG `backup@everlist.network` — **secret half lives ONLY in the A0
  container** `.secrets/gnupg` (never on the VPS). Public key on the box.
- VPS: `/usr/local/sbin/everlist-backup.sh` → `/root/backups/everlist-*.tar.gpg`
- Container: `ops/pull-backup.sh` → `backups-offsite/` (14 kept)
- Restore drill PASSED 2026-09-18: scp off box → gpg decrypt → tar → SQLite
  opens (15 listings) + env file inside.
- Full restore = scp bundle to a fresh box, decrypt, unpack hub.db beside
  app.py, recreate everlist.env from the bundle, restart.

## Systemd hardening

All three units (everlist, everlist-webchat, everlist-wrapper) run under
drop-ins `/etc/systemd/system/<unit>.d/harden.conf`: NoNewPrivileges,
ProtectSystem=strict, ProtectHome=tmpfs + BindPaths (deploy dir only),
PrivateTmp, kernel/controlgroup protections. Pre-change units backed up in
`/root/ops-backups/units-20260918-094639/`.

## Known limits (honest)

- Off-box copy = A0 container (same operator). True third-party offsite
  (Backblaze B2 etc.) = 5-min owner signup; then point pull-backup.sh at it.
- Email deliverability depends on Resend free tier (100/day).
- Container cron dies with the container; the A0 supervisor restarts it.

## Hardening pass 2 (2026-09-18, owner request)

| Layer | State |
| --- | --- |
| SSH | key-only (PasswordAuthentication no, first-match drop-in `00-everlist-hardening.conf` beats cloud-init's `50-`), X11 off, root login prohibit-password. Proven: fresh key login OK, password refused (`Permission denied (publickey)`) |
| ufw | 22/tcp now LIMIT (rate-limited), 80/443 allow; default deny incoming |
| fail2ban | installed, sshd jail on systemd backend, ban 1h / 5 tries / 10min. Already holding failed attempts |
| Web headers | HSTS(1y+subdomains), nosniff, SAMEORIGIN, strict-origin-when-cross-origin, Server stripped — live on everlist.network |
| Durability | `tools/deploy.sh` now renders headers AND the `/org/*` matcher (Caddy drift class closed at the source) |
| Backups | nightly payload now includes server configs (`conf.tar.gz`: Caddyfile, sshd drop-ins, fail2ban jail, cron, systemd units+drop-ins). Verified: pulled off-box, decrypted container-side, all parts open |

**Trap recorded:** sshd_config.d is FIRST-match-wins — `50-cloud-init.conf` silently shadowed `99-*.conf`. EverList drop-ins sort `00-`.

**Pending owner GO: kernel + 158 package updates installed but awaiting reboot** (running 6.8.0-31, installed 6.8.0-139). ~1 min downtime, any low-traffic time.
