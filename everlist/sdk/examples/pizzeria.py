#!/usr/bin/env python3
"""Pizzeria example — Agent Hub SDK quickstart (E2).

A merchant that:
  1. bootstraps its identity on the hub,
  2. publishes its menu as listings,
  3. polls for NEW orders (bookings) and prints them as they arrive.

Run:  python3 sdk/examples/pizzeria.py [hub_url]
"""
import os
import sys
import threading

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
from agenthub import AgentHub  # noqa: E402

MENU = [
    {"title": "Margherita", "price": 8.5, "capacity": 50},
    {"title": "Funghi", "price": 9.0, "capacity": 50},
    {"title": "Diavola", "price": 10.5, "capacity": 30},
]


def main():
    hub_url = sys.argv[1] if len(sys.argv) > 1 else "http://localhost:8802"
    hub = AgentHub(hub_url)

    print(f"* connecting to {hub_url}")
    man = hub.manifest()
    print(f"  hub={man['hub']} fee={man['fairness']['fee_policy']['actual_fee_pct']}% escrow={man['fairness']['escrow']}")

    # TRY_KEYPAIR=1: demo Tier-1 cryptographic account instead of interim /access.
    # The seed is generated locally and shown once — the hub stores only the pubkey.
    if os.environ.get("TRY_KEYPAIR"):
        print("* creating Tier-1 keypair account (seed stays local; hub stores pubkey only)")
        acct = hub.signup_keypair("pizzeria-napoli")
        print(f"  account {acct['account_id']} — SEED (store it!): {acct['seed']}")
    else:
        print("* bootstrapping merchant identity (interim /access; production = personhood)")
        hub.bootstrap("pizzeria-napoli", acts=("book", "list"))

    print("* publishing menu")
    for item in MENU:
        listing = hub.add_listing(
            vertical="food", title=item["title"], price=item["price"],
            capacity=item["capacity"], merchant="Pizzeria Napoli",
            preparation_minutes=20)
        print(f"  + {listing.title}: €{listing.price} (id={listing.id})")

    stop = threading.Event()

    def order(b):
        print(f"\n=== NEW ORDER {b.id} ===")
        print(f"  item listing: {b.listing_id}  qty: {b.quantity}")
        print(f"  buyer pays €{b.amount:.2f} | hub fee €{b.hub_fee:.2f} | you receive €{b.owner_payout:.2f}")
        print(f"  escrow: {b.escrow} (held until you confirm fulfillment)")
        print(f"  buyer ref: {b.extra.get('buyer') or b.extra.get('attendee')} (pseudonymous)")

    print("* waiting for orders (Ctrl+C to stop)...")
    seen = set()
    try:
        while not stop.is_set():
            for o in hub.orders():          # merchant view: incoming orders for MY listings
                if o.id not in seen:
                    seen.add(o.id)
                    order(o)
            stop.wait(2.0)
    except KeyboardInterrupt:
        stop.set()
    print("* bye")


if __name__ == "__main__":
    main()
