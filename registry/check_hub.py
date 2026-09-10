#!/usr/bin/env python3
"""D3: hub conformance cross-check script.

Fetches a hub's manifest + public ledger and verifies SELF-REPORTED
consistency (honesty note: consistency is NOT a fairness proof - see SPEC):

  C1  manifest declares fee policy + ledger endpoint
  C2  every booking ledger entry satisfies amount == hub_fee + owner_payout
      (arithmetic integrity, within rounding tolerance)
  C3  declared fee consistent: hub_fee == round(amount * pct/100, 2)
      within documented rounding/refund tolerance (refunds keep original
      split; x402 settlement events are separate kind, excluded)
  C4  escrow states are exactly HELD/RELEASED/REFUNDED/WAIVED and reported
      separately in a state breakdown; WAIVED only valid at amount 0 (C4b)
  C5  totals in /ledger response match recomputation from raw entries
  C6  manifest advertising accounts must serve the SPEC 12a challenge
      contract (GET <auth.challenge>?kind=signup -> algo/challenge/
      difficulty/ttl); hubs not advertising accounts skip this check

Output verdicts:
  CONFORMANT            all checks pass
  NON-CONFORMANT        any inconsistency found (exit 1)
  INSUFFICIENT-EVIDENCE cannot verify (empty ledger, missing endpoints)

Usage: check_hub.py <hub_url>
"""
import http.client
import json
import sys
import urllib.error
import urllib.parse
import urllib.request

TIMEOUT = 10.0
TOL = 0.011  # rounding tolerance (cents)


def fetch_json(url):
    req = urllib.request.Request(url, headers={"User-Agent": "agent-hub-conformance/0.1"})
    with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
        return json.loads(resp.read().decode())


def check_hub(base_url):
    findings = []
    verdict = "CONFORMANT"

    # C1: manifest
    try:
        man = fetch_json(base_url.rstrip("/") + "/.well-known/agent-hub.json")
    except (urllib.error.URLError, http.client.HTTPException, OSError, ValueError) as ex:
        return "INSUFFICIENT-EVIDENCE", [f"manifest unreachable: {ex}"]
    fee_pct = man.get("fairness", {}).get("fee_policy", {}).get("actual_fee_pct")
    led_path = man.get("fairness", {}).get("ledger")
    if not isinstance(fee_pct, (int, float)):
        return "INSUFFICIENT-EVIDENCE", ["manifest declares no actual_fee_pct"]
    if not isinstance(led_path, str) or not led_path.startswith("/"):
        return "INSUFFICIENT-EVIDENCE", ["manifest declares no relative ledger endpoint"]
    findings.append(f"declared fee: {fee_pct}% | ledger: {led_path}")

    try:
        led = fetch_json(base_url.rstrip("/") + led_path)
    except (urllib.error.URLError, http.client.HTTPException, OSError, ValueError) as ex:
        return "INSUFFICIENT-EVIDENCE", findings + [f"ledger unreachable: {ex}"]
    entries = [e for e in led.get("ledger", []) if e.get("kind") != "x402_settlement"]
    if not entries:
        return "INSUFFICIENT-EVIDENCE", findings + [
            "ledger has no booking entries - nothing to verify yet"]

    # C2 + C3: arithmetic + declared-fee consistency
    bad_arith = bad_fee = 0
    for e in entries:
        amount, fee, payout = e.get("amount", 0), e.get("hub_fee", 0), e.get("owner_payout", 0)
        if abs((fee + payout) - amount) > TOL:
            bad_arith += 1
        if abs(fee - round(amount * fee_pct / 100, 2)) > TOL:
            bad_fee += 1
    findings.append(f"entries checked: {len(entries)} | arithmetic mismatches: {bad_arith} "
                    f"| fee mismatches vs declared {fee_pct}%: {bad_fee}")
    if bad_arith:
        verdict = "NON-CONFORMANT"
        findings.append("C2 FAIL: hub_fee + owner_payout != amount on some entries")
    if bad_fee:
        verdict = "NON-CONFORMANT"
        findings.append("C3 FAIL: hub_fee does not match declared fee policy")

    # C4: escrow state separation
    states = {}
    for e in entries:
        states[e.get("escrow", "?")] = states.get(e.get("escrow", "?"), 0) + 1
    unknown_states = [s for s in states if s not in ("HELD", "RELEASED", "REFUNDED", "WAIVED")]
    findings.append(f"escrow breakdown: {states}")
    if unknown_states:
        verdict = "NON-CONFORMANT"
        findings.append(f"C4 FAIL: unknown escrow states: {unknown_states}")
    # C4b: WAIVED is only valid for genuinely free bookings (amount must be 0)
    waived_paid = [e for e in entries if e.get("escrow") == "WAIVED" and float(e.get("amount", -1)) != 0]
    if waived_paid:
        verdict = "NON-CONFORMANT"
        findings.append(f"C4b FAIL: {len(waived_paid)} WAIVED booking(s) with nonzero amount")

    # C5: totals vs recomputation
    totals = led.get("totals", {})
    recomp_vol = round(sum(e.get("amount", 0) for e in entries), 2)
    recomp_fees = round(sum(e.get("hub_fee", 0) for e in entries), 2)
    if abs(totals.get("total_volume", -1) - recomp_vol) > TOL \
            or abs(totals.get("total_hub_fees", -1) - recomp_fees) > TOL:
        verdict = "NON-CONFORMANT"
        findings.append(f"C5 FAIL: reported totals {totals} != recomputed "
                        f"(volume {recomp_vol}, fees {recomp_fees})")
    else:
        findings.append(f"C5 ok: totals match recomputation ({totals})")

    # C6 (B9): accounts advertising vs served crypto contract (SPEC 12a).
    # A hub that ADVERTISES accounts must serve the challenge contract;
    # hubs without accounts make no claim (auth optional, no flag).
    auth = man.get("auth")
    if auth:
        ch_path = auth.get("challenge") if isinstance(auth, dict) else None
        if not isinstance(ch_path, str) or not ch_path.startswith("/"):
            verdict = "NON-CONFORMANT"
            findings.append("C6 FAIL: accounts advertised but challenge endpoint missing/invalid")
        else:
            try:
                ch = fetch_json(base_url.rstrip("/") + ch_path + "?kind=signup")
            except (urllib.error.URLError, http.client.HTTPException, OSError, ValueError) as ex:
                ch = None
                findings.append(f"C6 challenge unreachable: {ex}")
            if not isinstance(ch, dict) or not ch.get("challenge") \
                    or not isinstance(ch.get("difficulty"), int) or not ch.get("algo") \
                    or not isinstance(ch.get("ttl"), int):
                verdict = "NON-CONFORMANT"
                findings.append("C6 FAIL: accounts advertised but challenge contract malformed (SPEC 12a)")
            else:
                findings.append(f"C6 ok: crypto challenge served (algo={ch['algo']}, "
                                f"difficulty={ch['difficulty']}, ttl={ch['ttl']}s)")
    else:
        findings.append("C6 skipped: no accounts advertised (auth optional)")

    findings.append("HONESTY: self-reported consistency != fairness proof; "
                    "independent settlement evidence does not exist at this stage (SPEC)")
    return verdict, findings


def main():
    if len(sys.argv) != 2:
        print(__doc__)
        sys.exit(2)
    verdict, findings = check_hub(sys.argv[1])
    print(f"Hub: {sys.argv[1]}")
    for f in findings:
        print(f"  - {f}")
    print(f"VERDICT: {verdict}")
    sys.exit(1 if verdict == "NON-CONFORMANT" else 0)


if __name__ == "__main__":
    main()
