#!/bin/bash
# EverList owner tool: install the current deploy key from the server console.
# Built for fetch-and-run on consoles that mangle shifted characters (& _ > '):
#   wget https://raw.githubusercontent.com/everlist-hq/everlist/main/everlist/tools/unlock-root.sh
#   bash unlock-root.sh
# Only ever APPENDS a PUBLIC key (public material) to /root/.ssh/authorized_keys.
set -u
mkdir -p /root/.ssh
chmod 700 /root/.ssh
touch /root/.ssh/authorized_keys
chmod 600 /root/.ssh/authorized_keys
KEY='ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIBZ6dQa/oGvrcu+kjL4dgzU+WdchycEqD+ZKnhljPGV0 a0-container-deploy-2026-09-15'
grep -qxF "$KEY" /root/.ssh/authorized_keys || echo "$KEY" >> /root/.ssh/authorized_keys
# tidy the junk left by the garbled v0.4 paste (dirs named 77, echo, .sh, ...)
rm -rf /root/.sh /root/77 /root/echo /root/ssh-ed25519 /root/a0-container-deploy-2026-09-15 /root/AAAAC3* 2>/dev/null || true
echo '--- NEW KEY INSTALLED ---'
ls -la /root
