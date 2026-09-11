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

Registry document verification (C6):
  check_hub.py --registry <registry_url> [--expect-hub <hub_url>]
               [--pubkey <pinned_hex>]
  verifies the Ed25519 signature over the canonical payload of
  GET <registry_url>/registry.json; --pubkey enforces out-of-band
  key pinning (NON-CONFORMANT on mismatch).

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


def canonical_payload_bytes(payload) -> bytes:
    """C6: THE canonical form - must match registry.py exactly (sort_keys +
    tight separators). Signer and verifier share this one expression."""
    return json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")


def verify_envelope(doc: dict) -> tuple:
    """C6: verify a signed registry document. Returns (ok, payload, reason).
    Signature: ed25519 over the canonical payload bytes, made with the key
    embedded in the envelope (out-of-band pinning is the production upgrade;
    the format field documents this honestly)."""
    if not isinstance(doc, dict):
        return False, None, "document not an object"
    if doc.get("format") != "everlist-registry/1-signed":
        return False, None, f"unknown format: {doc.get('format')!r}"
    payload = doc.get("payload")
    sig = doc.get("signature")
    if not isinstance(payload, dict) or not isinstance(sig, dict):
        return False, None, "payload/signature missing or malformed"
    if sig.get("algo") != "ed25519":
        return False, None, f"unknown signature algo: {sig.get('algo')!r}"
    pk_hex, sig_hex = sig.get("public_key", ""), sig.get("sig", "")
    if not (isinstance(pk_hex, str) and len(pk_hex) == 64
            and all(c in "0123456789abcdef" for c in pk_hex)):
        return False, None, "public_key must be 64 hex chars"
    if not (isinstance(sig_hex, str) and 120 <= len(sig_hex) <= 130
            and all(c in "0123456789abcdef" for c in sig_hex)):
        return False, None, "sig must be hex"
    try:
        from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
        from cryptography.exceptions import InvalidSignature
        pub = Ed25519PublicKey.from_public_bytes(bytes.fromhex(pk_hex))
        pub.verify(bytes.fromhex(sig_hex), canonical_payload_bytes(payload))
    except InvalidSignature:
        return False, None, "signature INVALID (payload tampered or wrong key)"
    except Exception as ex:
        return False, None, f"verification error: {ex}"
    if payload.get("format") != "everlist-registry/1" or not isinstance(payload.get("hubs"), list):
        return False, None, "payload shape invalid"
    return True, payload, ""


def check_registry(registry_url: str, expect_hub: str | None = None,
                   pinned_pubkey: str | None = None) -> tuple:
    """C6: fetch + verify a registry document; optionally require a hub URL to
    be listed and/or a PINNED public key (out-of-band trust). Returns
    (verdict, findings)."""
    findings = []
    try:
        doc = fetch_json(registry_url.rstrip("/") + "/registry.json")
    except (urllib.error.URLError, http.client.HTTPException, OSError, ValueError) as ex:
        return "INSUFFICIENT-EVIDENCE", [f"registry.json unreachable: {ex}"]
    ok, payload, why = verify_envelope(doc)
    if not ok:
        return "NON-CONFORMANT", [f"C6-REG FAIL: signature verification failed: {why}"]
    findings.append(f"C6-REG: signature VALID (ed25519, key {doc['signature']['public_key'][:16]}...) - "
                    f"{payload.get('hub_count')} hub(s), generated_unix={payload.get('generated_unix')}")
    if pinned_pubkey:
        if doc["signature"]["public_key"] != pinned_pubkey.strip().lower():
            return "NON-CONFORMANT", findings + [
                "C6-REG FAIL: document signed with a DIFFERENT key than the pinned one "
                "(possible key substitution)"]
        findings.append("C6-REG: embedded key MATCHES the pinned public key")
    if expect_hub:
        want = expect_hub.rstrip("/")
        hit = next((h for h in payload["hubs"] if h.get("url", "").rstrip("/") == want), None)
        if not hit:
            return "NON-CONFORMANT", findings + [
                f"C6-REG FAIL: {want} not listed in the signed document"]
        findings.append(f"C6-REG: hub {want} listed (tier={hit.get('tier')}, "
                        f"protocol={hit.get('protocol')})")
    findings.append("C6-REG NOTE: key is embedded in the envelope; production pinning "
                    "= compare against /registry.pub fetched out-of-band")
    return "CONFORMANT", findings


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
    # separate-kind ledger events carry their own shape (no escrow key): the
    # x402 settlements since E-phase, and the M7 escrow_sync mirror entries
    # (from/to/chain_tx) - excluded from booking-escrow state accounting
    entries = [e for e in led.get("ledger", [])
               if e.get("kind") not in ("x402_settlement", "escrow_sync")]
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

    # C7 (M7): escrow mirror declared + endpoint real + gated. The checker is
    # unauthenticated by design, so full hub-vs-chain state consistency is NOT
    # independently verifiable here (needs admin credentials) - recorded as a
    # limitation, matching the HONESTY note below.
    mirror = (man.get("fairness") or {}).get("mirror") or {}
    sync_path = mirror.get("sync")
    if not isinstance(sync_path, str) or not sync_path.startswith("/"):
        findings.append("C7 FAIL: manifest declares no fairness.mirror.sync endpoint")
        verdict = "NON-CONFORMANT"
    else:
        policy = str(mirror.get("policy", ""))
        if "source of truth" not in policy or "downward" not in policy:
            findings.append("C7 FAIL: mirror policy must declare chain-as-source-of-truth + never-overwrite-downward")
            verdict = "NON-CONFORMANT"
        else:
            findings.append(f"C7: mirror declared ({mirror.get('rail', '?')}), policy OK")
        try:
            req = urllib.request.Request(base_url.rstrip("/") + sync_path, method="POST",
                data=json.dumps({}).encode(), headers={"Content-Type": "application/json"})
            with urllib.request.urlopen(req, timeout=10) as resp:
                findings.append(f"C7 FAIL: {sync_path} answered {resp.status} WITHOUT admin key (must be gated)")
                verdict = "NON-CONFORMANT"
        except urllib.error.HTTPError as ex:
            if ex.code == 403:
                findings.append("C7: sync endpoint gated (403 without admin key) - OK")
            else:
                findings.append(f"C7 FAIL: {sync_path} answered {ex.code} without admin key (expected 403)")
                verdict = "NON-CONFORMANT"
        except (urllib.error.URLError, http.client.HTTPException, OSError) as ex:
            findings.append(f"C7 WARN: sync endpoint unreachable: {ex}")
    findings.append("C7 NOTE: hub-vs-chain state consistency is admin-verifiable only "
                    "(POST /admin/sync-escrow with X-Admin-Key); anonymous checkers "
                    "verify declaration + gating, not live parity")
    findings.append("HONESTY: self-reported consistency != fairness proof; "
                    "independent settlement evidence does not exist at this stage (SPEC)")
    return verdict, findings


def main():
    args = sys.argv[1:]
    if not args:
        print(__doc__)
        sys.exit(2)
    if args[0] == "--registry":
        # C6: verify the signed registry document instead of checking a hub
        if len(args) < 2:
            print(__doc__)
            sys.exit(2)
        reg_url = args[1]
        expect_hub = pinned = None
        i = 2
        while i < len(args):
            if args[i] == "--expect-hub" and i + 1 < len(args):
                expect_hub = args[i + 1]
                i += 2
            elif args[i] == "--pubkey" and i + 1 < len(args):
                pinned = args[i + 1]
                i += 2
            else:
                print(f"unknown option: {args[i]}")
                sys.exit(2)
        verdict, findings = check_registry(reg_url, expect_hub, pinned)
        print(f"Registry: {reg_url}")
    else:
        if len(args) != 1:
            print(__doc__)
            sys.exit(2)
        verdict, findings = check_hub(args[0])
        print(f"Hub: {args[0]}")
    for f in findings:
        print(f"  - {f}")
    print(f"VERDICT: {verdict}")
    sys.exit(1 if verdict == "NON-CONFORMANT" else 0)


if __name__ == "__main__":
    main()
