# noVNC Paste Button (CloudServer / SolusVM)

The provider's noVNC console has no clipboard bridge, so commands must be typed by hand — which made server recovery (e.g. installing a new SSH key after a key rotation) painful.

This Tampermonkey userscript injects a **📋 Paste** button into the noVNC status bar. Clicking it sends your clipboard (or a prompt-box fallback) into the console character-by-character via `window.rfb.sendKey`.

## Install (once per browser)

1. Install the [Tampermonkey](https://www.tampermonkey.net/) extension
2. Tampermonkey → Dashboard → **+** (new script)
3. Paste the contents of `novnc-paste.user.js`, save
4. Open the VPS **noVNC Console** from the cloudserver panel — the **📋 Paste** button appears in the console status bar

## Use

1. Copy the command you need (e.g. the SSH key install line from the current `docs/ops/` note or from the agent chat)
2. Click **📋 Paste** — if the browser blocks clipboard read, the script falls back to a paste-into-prompt box
3. Press **Enter** in the console if the pasted line didn't include a trailing newline

## Caveats

- Types ~8 ms/character; very long pastes take a few seconds — don't type while it's sending
- The prompt-box fallback also bypasses Chrome clipboard-permission prompts entirely (often the easiest route)
- If the button doesn't appear, hard-refresh the console page (Ctrl+Shift+R) so Tampermonkey re-injects

## Why we needed it (2026-09-15)

The Agent Zero container was rebuilt, rotating our deploy key; the server (key-only SSH) rejected the new key. Re-authorization required typing/pasting the new public key at the server console — this script is the paste path. The new keypair is backed up persistently at `/a0/usr/ssh/` so future rebuilds won't need this dance again.

## Changelog

- **v0.5 (2026-09-15): shift-safe.** v0.4 never held Shift, so `&&` typed as `77`, `authorized_keys` as `authorized-keys`, and the 8 ms release-timer race dropped characters outright — the server ran a garbled `mkdir` and the key never landed. v0.5 synthesizes Shift for `& _ > " | ( )` and uppercase, types at 30 ms/char, and releases keys immediately (no timer overlap). Button now reads `📋 Paste v0.5`. Test first with `echo v05-test` + Enter — the console must echo `v05-test` exactly.
