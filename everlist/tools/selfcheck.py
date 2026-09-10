#!/usr/bin/env python3
"""H14: EverList hub self-check — read-only health probe (on-demand; no scheduler).

Works against ANY hub URL (local test hub, this machine, or a deployed VPS):

    python tools/selfcheck.py                          # http://127.0.0.1:8802
    HUB_URL=https://everlist.network python tools/selfcheck.py

Checks (all read-only GETs): discovery manifest, OpenAPI contract + linkage,
verticals, listings, search, ledger, request-id echo (H12).
Exit 0 = all green; exit 1 = at least one check failed (printed, never a trace).
"""
import json
import os
import sys
import urllib.error
import urllib.request

BASE = os.environ.get("HUB_URL", "http://127.0.0.1:8802").rstrip("/")


def get(path):
    try:
        with urllib.request.urlopen(BASE + path, timeout=10) as r:
            body = r.read().decode()
            try:
                parsed = json.loads(body)
            except Exception:
                parsed = {}
            return r.status, parsed, dict(r.headers)
    except urllib.error.HTTPError as e:
        return e.code, {}, {}
    except Exception as e:
        return -1, {"error": str(e)}, {}


def main():
    results = []

    def check(name, ok, detail=""):
        results.append(bool(ok))
        print(("PASS" if ok else "FAIL"), name, ("" if ok else f"- {detail}"))

    print(f"EverList self-check -> {BASE}\n")
    st, man, _ = get("/.well-known/agent-hub.json")
    check("discovery manifest (/.well-known/agent-hub.json)",
          st == 200 and bool(man.get("protocol")), f"HTTP {st}")
    st, api, _ = get("/openapi.json")
    check("OpenAPI contract (/openapi.json)",
          st == 200 and str(api.get("openapi", "")).startswith("3.1"), f"HTTP {st}")
    check("manifest links the API contract",
          man.get("api_contract") == "/openapi.json", str(man.get("api_contract")))
    st, vert, _ = get("/verticals")
    check("vertical schema registry", st == 200 and bool(vert.get("verticals")), f"HTTP {st}")
    st, lst, _ = get("/listings")
    check("public listings", st == 200 and isinstance(lst.get("listings"), list), f"HTTP {st}")
    st, sr, _ = get("/search?q=test")
    check("search", st == 200, f"HTTP {st}")
    st, led, _ = get("/ledger")
    check("public money ledger", st == 200, f"HTTP {st}")
    st, _, hdrs = get("/verticals")
    check("request-id echo (traceability)", str(hdrs.get("X-Request-Id", "")).startswith("req-"),
          str(hdrs.get("X-Request-Id", "missing"))[:20])

    ok = all(results)
    print(f"\n{'ALL GREEN' if ok else 'PROBLEMS FOUND'} "
          f"({sum(results)}/{len(results)} checks) for {BASE}")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
