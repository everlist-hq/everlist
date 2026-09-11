"""M10: chain secret hygiene - red-team scan that seed material NEVER
appears in runtime artifacts, git content, or hub storage.
Done-when: doc (PRIVACY.md Midnight threat model) + red-team check that no
seed material appears in state.json/logs/repo. Run: python test_m10_secrets.py

Layout-portable (lesson from CI): the operator tree has .secrets/ + live
state -> FULL teeth; a fresh published clone has neither -> structural mode
(git-tree scan ALWAYS runs; runtime scans note their absence instead of
failing - absence of artifacts is not a leak).
"""
import os
import re
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
RESULTS = []

def check(name, cond, detail=""):
    RESULTS.append((name, bool(cond)))
    print(("PASS" if cond else "FAIL"), name, ("" if cond else detail))

# --- environment resolution (portable: experiments/ tree AND published repo) ---
_SECRET_DIR = os.path.join(HERE, ".secrets")
_SEED_CANDIDATES = [
    os.path.join(_SECRET_DIR, "agent_seed"),                 # operator tree
    os.path.join(HERE, "..", "..", "experiments", "agent-hub-v2",
                 ".secrets", "agent_seed"),                  # from published repo
]
OPERATOR_ENV = os.path.isdir(_SECRET_DIR)
seed_path = next((p for p in _SEED_CANDIDATES if os.path.exists(p)), None)

SEED = ""
if OPERATOR_ENV:
    if seed_path:
        SEED = open(seed_path).read().strip()
    check("canonical seed exists and is loadable", bool(SEED),
          "operator .secrets/ present but agent_seed missing/unreadable")
else:
    check("fresh clone: no .secrets dir -> structural scan mode", True,
          "(nothing operator-secret exists in this checkout)")

SECRET_FILES = {}
if OPERATOR_ENV:
    for f in ("agent_seed", "agentverse_key", "email.env"):
        p = os.path.join(_SECRET_DIR, f)
        if os.path.exists(p):
            SECRET_FILES[f] = open(p, "rb").read()

# hex-y tokens worth flagging if they appear in state/logs (64-hex = seed-sized)
SECRET_TOKENS = {f: [ln.strip() for ln in c.decode(errors="replace").splitlines()
                     if re.fullmatch(r"[0-9a-fA-F]{32,}", ln.strip())]
                 for f, c in SECRET_FILES.items()}

def scan_file(path, patterns, label):
    """Substring OR compiled-regex patterns (regex = secret-SHAPED values)."""
    try:
        blob = open(path, "rb").read()
    except OSError:
        return None
    text = blob.decode(errors="replace")
    for name, pat in patterns.items():
        if isinstance(pat, re.Pattern):
            if pat.search(text):
                return "%s contains %s" % (label, name)
        elif pat and pat in text:
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

print("== M10: git tree ==")
# resolve the ACTUAL git repo(s) from this file's location: the published repo
# root (parent of everlist/) when running from the published layout, or the
# devlab staging repo when running from the experiments tree
_REPO_CANDIDATES = [os.path.join(HERE, ".."),
                    os.path.join(HERE, "..", "..", ".staging-repo")]
_seen = set()
REPOS = []
for _c in _REPO_CANDIDATES:
    _c = os.path.normpath(os.path.abspath(_c))
    if os.path.isdir(os.path.join(_c, ".git")) and _c not in _seen:
        _seen.add(_c)
        REPOS.append(_c)
check("git repository located for tracked-content scan", bool(REPOS))
for repo in REPOS:
    rname = os.path.basename(os.path.normpath(repo))
    out = subprocess.run(["git", "ls-files"], cwd=repo, capture_output=True, text=True)
    tracked = [l for l in out.stdout.splitlines() if l.strip()]
    bad_paths = [l for l in tracked if ".secrets" in l or l.endswith("state.json") or ".run/" in l]
    check("no secret/state paths tracked in %s" % rname, not bad_paths, str(bad_paths[:3]))
    content_leaks = []
    for l in tracked:
        p = os.path.join(repo, l)
        # tracked content: flag secret-SHAPED values (the seed itself, real
        # tokens, elseed-<32+hex>) - NOT bare marker mentions, which docs and
        # code legitimately contain (e.g. chatlib removeprefix, PRIVACY.md)
        r = scan_file(p, {"canonical seed": SEED,
                          "elseed-<32+hex> secret": re.compile(r"elseed-[0-9a-fA-F]{32,}"),
                          **{"%s token" % f: tok for f, toks in SECRET_TOKENS.items() for tok in toks}}, l)
        if r:
            content_leaks.append(r)
    check("no seed material in tracked file content (%s, %d files)" % (rname, len(tracked)),
          not content_leaks, str(content_leaks[:3]))

print("== M10: hub storage structure ==")
state = None
if os.path.exists(state_path):
    import json
    try:
        state = json.load(open(state_path))
    except Exception:
        pass
if state is not None:
    BAD_FIELD = {"seed", "secret", "sk", "private_key", "mnemonic"}
    bad = []
    for kind in ("accounts", "listings", "bookings"):
        for k, v in (state.get(kind) or {}).items() if isinstance(state.get(kind), dict) else enumerate(state.get(kind) or []):
            if isinstance(v, dict):
                hit = BAD_FIELD & set(v)
                if hit:
                    bad.append("%s/%s: %s" % (kind, k, sorted(hit)))
    check("no secret-named fields in persisted hub entities", not bad, str(bad[:3]))
elif os.path.exists(state_path):
    check("hub state parsable for structure scan", False, "state.json present but unreadable")
else:
    check("no runtime state in this checkout (structure scan skipped)", True,
          "(state.json appears once the hub persists; the scan runs then)")

print("== M10: .secrets permissions ==")
if OPERATOR_ENV:
    perms_ok = True
    for f in os.listdir(_SECRET_DIR):
        mode = oct(os.stat(os.path.join(_SECRET_DIR, f)).st_mode & 0o777)
        if mode not in ("0o600", "0o400"):
            perms_ok = False
            print("   [m10] %s is %s (want 600)" % (f, mode))
    check(".secrets files are 0600/0400", perms_ok)
else:
    check("no .secrets dir in this checkout (nothing to lock down)", True)

fails = [n for n, ok in RESULTS if not ok]
print("")
print("=== m10-secrets: %d/%d passed ===" % (len(RESULTS) - len(fails), len(RESULTS)))
if fails:
    print("FAILED:", fails); sys.exit(1)
print("M10_SECRETS_ALL_PASSED")
