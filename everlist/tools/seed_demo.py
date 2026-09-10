#!/usr/bin/env python3
"""H16: seed a fresh EverList hub with realistic pilot-demo listings.

One command (from the repo root):

    make demo-seed     # seed the running hub with demo_listings.json
    make demo-reset    # stop hub -> archive demo state -> start fresh -> seed

The tool itself is pure HTTP (works against any hub URL):

    python tools/seed_demo.py --hub http://127.0.0.1:8802

Listings come from demo_listings.json (events + services verticals, including
the free Surya Kriya yoga class). Every listing is owned by a deterministic
demo agent principal (demo-<slug>), so re-seeding a FRESH hub always produces
the same demo. Seeding an already-seeded hub is refused (use demo-reset).
"""
import argparse
import json
import os
import sys
import urllib.error
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
DEFAULT_SEED = os.path.join(HERE, os.pardir, "demo_listings.json")


def api(base, method, path, body=None, token=None):
    data = json.dumps(body).encode() if body is not None else None
    r = urllib.request.Request(base.rstrip("/") + path, data=data, method=method,
                               headers={"Content-Type": "application/json"})
    if token:
        r.add_header("X-Hub-Token", token)
    try:
        with urllib.request.urlopen(r, timeout=10) as resp:
            return resp.status, json.loads(resp.read().decode())
    except urllib.error.HTTPError as e:
        try:
            return e.code, json.loads(e.read().decode())
        except Exception:
            return e.code, {}
    except Exception as e:
        return -1, {"error": str(e)}


def main():
    ap = argparse.ArgumentParser(description="Seed EverList demo listings")
    ap.add_argument("--hub", default=os.environ.get("HUB_URL", "http://127.0.0.1:8802"))
    ap.add_argument("--file", default=DEFAULT_SEED)
    args = ap.parse_args()

    seed = json.load(open(args.file))
    st, existing = api(args.hub, "GET", "/listings")
    if st != 200:
        print(f"hub unreachable at {args.hub} (HTTP {st}) — start it with 'make up'")
        return 1
    if existing.get("listings"):
        print(f"hub already has {len(existing['listings'])} listing(s) — "
              "refusing to seed on top (use 'make demo-reset' for a fresh demo)")
        return 1

    created = []
    for item in seed["listings"]:
        agent = "demo-" + item["slug"]
        st, acc = api(args.hub, "POST", "/access", {"agent": agent, "acts": ["list"]})
        if st not in (200, 201):  # /access mints tokens -> 201 Created
            print(f"FAIL token for {agent}: {acc}")
            return 1
        payload = {k: v for k, v in item.items() if k not in ("slug",)}
        payload["source"] = "demo-seed"
        st, res = api(args.hub, "POST", "/listings", payload, token=acc["tokens"]["list"])
        if st != 201:
            print(f"FAIL listing {item.get('title')}: {res}")
            return 1
        created.append(res["id"])
        mc = res.get("manage_code", "")
        print(f"  seeded {res['id']:>10}  {item['vertical']:<8}  {item['title']}"
              + (f"  manage:{mc}" if mc else ""))
    print(f"\nDemo ready: {len(created)} listings at {args.hub}")
    print("Try: search yoga | book <free listing id> (chat) | SDK examples/pizzeria.py")
    return 0


if __name__ == "__main__":
    sys.exit(main())
