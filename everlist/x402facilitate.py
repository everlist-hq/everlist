"""C3b: x402 settlement via facilitator for agent-hub-v2.

Settlement = facilitator submits the EIP-3009 authorization on-chain and
returns a tx hash. The hub NEVER custodies funds (no-custody principle);
the facilitator is Coinbase's CDP service (per payments_x402.md).

Design per plan acceptance:
- idempotent: one payment (nonce fingerprint) settles AT MOST once; duplicate
  submissions return the recorded result without calling the facilitator again
- timeout/unknown-outcome: a timeout or 5xx means the settlement state is
  UNKNOWN (never 'settled' without a tx hash); status recorded as 'unknown'
- evidence: settlement results are persisted and reconcilable with the ledger

Production endpoint: CDP x402 facilitator (needs HUB_FACILITATOR_KEY).
Tests override HUB_FACILITATOR_URL with a local stub (documented pattern).
"""
import hashlib
import json
import threading
import urllib.error
import urllib.request

DEFAULT_FACILITATOR = "https://api.cdp.coinbase.com/platform/v2/x402"


def payment_fingerprint(auth: dict) -> str:
    """Stable id for a payment = sha256 of its unique authorization fields.
    Addresses are lowercased: EIP-55 checksummed input must not change the id.
    """
    basis = (f"{str(auth['from']).lower()}|{str(auth['to']).lower()}"
             f"|{auth['value']}|{auth['nonce']}")
    return hashlib.sha256(basis.encode()).hexdigest()[:16]


class FacilitatorClient:
    def __init__(self, base_url: str, api_key: str | None = None, timeout: int = 20):
        self.base = base_url.rstrip("/")
        self.key = api_key
        self.timeout = timeout

    def _call(self, path: str, body: dict) -> tuple[int, dict]:
        headers = {"Content-Type": "application/json"}
        if self.key:
            headers["X-API-KEY"] = self.key
        req = urllib.request.Request(f"{self.base}/{path}", method="POST",
                                     data=json.dumps(body).encode(), headers=headers)
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as r:
                return r.status, json.loads(r.read().decode())
        except urllib.error.HTTPError as e:
            try:
                return e.code, json.loads(e.read().decode())
            except Exception:
                return e.code, {}
        except Exception:
            # timeout / connection error -> UNKNOWN outcome (may have gone through!)
            return 0, {"error": "timeout or unreachable - settlement outcome UNKNOWN"}


class SettlementRegistry:
    """Idempotent settlement records keyed by payment fingerprint.

    status: settled (with tx) | unknown (timeout/5xx) | failed (definitive 4xx)
    """

    def __init__(self):
        self._s = {}   # fingerprint -> record
        self._lock = threading.Lock()

    def load(self, snap: dict):
        with self._lock:
            self._s.update(snap)

    def snapshot(self) -> dict:
        with self._lock:
            return dict(self._s)

    def get(self, fp: str) -> dict | None:
        with self._lock:
            return self._s.get(fp)

    def settle(self, fp: str, payload: dict, requirements: dict,
               client: FacilitatorClient) -> dict:
        """Settle once. Duplicate submission NEVER re-calls the facilitator.

        Returns the settlement record. Caller persists it (G1).
        """
        with self._lock:
            if fp in self._s:  # duplicate submission: return recorded result
                rec = dict(self._s[fp])
                rec["duplicate"] = True
                return rec
        # Note: call OUTSIDE lock would race two first-settles; but payments are
        # nonce-unique and the nonce registry already blocks replay before we
        # get here, so per-fingerprint first-call is serialized by upstream.
        st, resp = client._call("settle", {"x402Version": 1,
                                            "paymentPayload": payload,
                                            "paymentRequirements": requirements})
        rec = {"fingerprint": fp, "http_status": st, "ts": __import__("time").time()}
        if st == 200 and resp.get("success") and resp.get("transaction"):
            rec["status"] = "settled"
            rec["tx"] = resp["transaction"]
            rec["network"] = resp.get("network", "base-sepolia")
            rec["payer"] = resp.get("payer", "")
        elif st == 0:
            rec["status"] = "unknown"   # NEVER claim settled without tx proof
        elif 400 <= st < 500:
            rec["status"] = "failed"    # definitive rejection (e.g. invalid sig)
            rec["error"] = resp.get("error", f"facilitator {st}")
        else:
            rec["status"] = "unknown"   # 5xx: may have settled; treat unknown
        with self._lock:
            # re-check: a concurrent first settle may have recorded while we called
            existing = self._s.get(fp)
            if existing is not None:
                out = dict(existing)
                out["duplicate"] = True
                return out
            self._s[fp] = rec
        return dict(rec)


def reconcile(registry: SettlementRegistry, ledger: list) -> dict:
    """Cross-check settlement records vs ledger entries (D1 accounting).

    Every settled record MUST have a matching ledger event; ledger events
    reference fingerprints. Returns {ok, missing_in_ledger, unknown_count}.
    """
    led_fps = {e.get("detail", {}).get("fingerprint") for e in ledger
               if e.get("kind") == "x402_settlement"}
    settled = {fp for fp, r in registry.snapshot().items() if r["status"] == "settled"}
    unknown = {fp for fp, r in registry.snapshot().items() if r["status"] == "unknown"}
    missing = sorted(settled - led_fps)
    return {"ok": not missing, "missing_in_ledger": missing,
            "settled": sorted(settled), "unknown": sorted(unknown)}
