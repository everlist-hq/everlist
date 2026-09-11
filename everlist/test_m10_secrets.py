"""M10: chain secret hygiene - red-team scan that seed material NEVER
appears in runtime artifacts, git content, or hub storage.
Done-when: doc (PRIVACY.md Midnight threat model) + red-team check that no
seed material appears in state.json/logs/repo. Run: python test_m10_secrets.py
"""
import os
import re
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
DEVLAB = os.path.dirname(HERE)  # experiments/
RESULTS = []

def check(name, cond, detail=""):
    RESULTS.append((name, bool(cond)))
    print(("PASS" if cond else "FAIL"), name, ("" if cond else detail))

SEED = ""
seed_path = os.path.join(HERE, ".secrets", "agent_seed")
if os.path.exists(seed_path):
    SEED = open(seed_path).read().strip()
check("canonical seed exists and is loadable", bool(SEED))

SECRET_FILES = {}
for f in ("agent_seed", "agentverse_key", "email.env"):
    p = os.path.join(HERE, ".secrets", f)
    if os.path.exists(p):
        SECRET_FILES[f] = open(p, "rb").read()

# hex-y tokens worth flagging if they appear in state/logs (64-hex = seed-sized)
SECRET_TOKENS = {f: [ln.strip() for ln in c.decode(errors="replace").splitlines()
                     if re.fullmatch(r"[0-9a-fA-F]{32,}", ln.strip())]
                 for f, c in SECRET_FILES.items()}

def scan_file(path, patterns, label):
    try:
        blob = open(path, "rb").read()
    except OSError:
        return None
    text = blob.decode(errors="replace")
    for name, pat in patterns.items():
        if pat and pat in text:
            return "%s contains %s" % (label, name)
    return None

print("== M10: runtime artifacts ==")
scan_targets = []
for root in (os.path.join(HERE, ".run"), os.path.join(HERE, "backups")):
    if os.path.isdir(root):
        for dirpath, _dirs, files in os.walk(root):
            scan_targets += [os.path.join(dirpath, f) for f in files]
for f in os.listdir(HERE):
    if f.endswith((".log", ".json")) and f.startswith("state"):
        scan_targets.append(os.path.join(HERE, f))
state_path = os.path.join(HERE, ".run", "state.json")
if os.path.exists(state_path):
    scan_targets.append(state_path)
leaks = [r for t in scan_targets
         if (r := scan_file(t, {"canonical seed": SEED,
                                "elseed- prefix": "elseed-",
                                **{"%s token" % f: tok for f, toks in SECRET_TOKENS.items() for tok in toks}},
                           os.path.relpath(t, HERE)))]
check("no seed material in state/logs/backups (%d files scanned)" % len(scan_targets),
      not leaks, str(leaks[:3]))

print("== M10: git tree (both repos) ==")
for repo in (os.path.join(DEVLAB, ".staging-repo"),):
    if not os.path.isdir(repo):
        continue
    out = subprocess.run(["git", "ls-files"], cwd=repo, capture_output=True, text=True)
    tracked = [l for l in out.stdout.splitlines() if l.strip()]
    bad_paths = [l for l in tracked if ".secrets" in l or l.endswith("state.json") or ".run/" in l]
    check("no secret/state paths tracked in %s" % os.path.basename(repo), not bad_paths, str(bad_paths[:3]))
    content_leaks = []
    for l in tracked:
        p = os.path.join(repo, l)
        r = scan_file(p, {"canonical seed": SEED, "elseed- prefix": "elseed-"}, l)
        if r:
            content_leaks.append(r)
    check("no seed material in tracked file content", not content_leaks, str(content_leaks[:3]))

print("== M10: hub storage structure ==")
state = None
if os.path.exists(state_path):
    import json
    try:
        state = json.load(open(state_path))
    except Exception:
        pass
if state:
    BAD_FIELD = {"seed", "secret", "sk", "private_key", "mnemonic"}
    bad = []
    for kind in ("accounts", "listings", "bookings"):
        for k, v in (state.get(kind) or {}).items() if isinstance(state.get(kind), dict) else enumerate(state.get(kind) or []):
            if isinstance(v, dict):
                hit = BAD_FIELD & set(v)
                if hit:
                    bad.append("%s/%s: %s" % (kind, k, sorted(hit)))
    check("no secret-named fields in persisted hub entities", not bad, str(bad[:3]))
else:
    check("hub state available for structure scan", False, "state.json missing/unreadable")

print("== M10: .secrets permissions ==")
perms_ok = True
for f in os.listdir(os.path.join(HERE, ".secrets")):
    mode = oct(os.stat(os.path.join(HERE, ".secrets", f)).st_mode & 0o777)
    if mode not in ("0o600", "0o400"):
        perms_ok = False
        print("   [m10] %s is %s (want 600)" % (f, mode))
check(".secrets files are 0600/0400", perms_ok)

fails = [n for n, ok in RESULTS if not ok]
print("")
print("=== m10-secrets: %d/%d passed ===" % (len(RESULTS) - len(fails), len(RESULTS)))
if fails:
    print("FAILED:", fails); sys.exit(1)
print("M10_SECRETS_ALL_PASSED")
