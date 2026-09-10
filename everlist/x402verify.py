"""Real x402 payment verification for agent-hub-v2 (C3a).

Verifies EIP-3009 TransferWithAuthorization payments (exact scheme, EVM)
against the wire format documented in payments_x402.md.

- Real EIP-712 signature recovery via eth-account (maintained lib; buy-vs-build).
- Nonce replay protection: used nonces are stored (persisted with hub state).
- This module VERIFIES; it does NOT settle on-chain (C3b does settlement via
  facilitator). No funds pass through the hub (no-custody principle).

Mode selection (HUB_PAY_MODE env on app.py):
  simulated -> stub acceptance, labeled SIMULATED (dev/demo only)
  testnet   -> THIS module: real crypto verification, testnet domain
  production-> same verification with mainnet domain/params (C3b+)
"""
import base64
import hashlib
import json
import threading
import time

from eth_account import Account
from eth_account.messages import encode_typed_data

# Official USDC domains per payments_x402.md (C1)
USDC_DOMAINS = {
    "base-sepolia": {"name": "USD Coin", "version": "2", "chainId": 84532,
                      "verifyingContract": "0x036CbD53842c5426634e7929541eC2318f3dCF7e"},
    "base": {"name": "USD Coin", "version": "2", "chainId": 8453,
              "verifyingContract": "0x833589fCD6eDb6E08f4c7C32D4f71b54bdA02913"},
}

TRANSFER_TYPES = {"TransferWithAuthorization": [
    {"name": "from", "type": "address"}, {"name": "to", "type": "address"},
    {"name": "value", "type": "uint256"}, {"name": "validAfter", "type": "uint256"},
    {"name": "validBefore", "type": "uint256"}, {"name": "nonce", "type": "bytes32"}]}

_USED_NONCES = {}          # nonce -> exp (replay registry)
_NLOCK = threading.Lock()


def _prune():
    now = time.time()
    for n in [n for n, exp in _USED_NONCES.items() if exp < now]:
        _USED_NONCES.pop(n, None)


def load_used_nonces(snapshot: dict):
    """Restore persisted nonce registry (G1-style persistence)."""
    with _NLOCK:
        _USED_NONCES.update(snapshot)


def snapshot_used_nonces() -> dict:
    with _NLOCK:
        _prune()
        return dict(_USED_NONCES)


def verify_payment(x_payment_header: str, *, network: str,
                   pay_to: str, max_amount_units: int,
                   skew_seconds: int = 600) -> tuple[dict | None, str | None]:
    """Verify an X-PAYMENT header value against the offer terms.

    Returns (payment_info, None) on success, (None, reason) on any failure.
    payment_info = {from, value, nonce, network}
    """
    try:
        raw = base64.b64decode(x_payment_header)
        payload = json.loads(raw.decode())
    except Exception as ex:
        return None, f"undecodable payment payload: {ex}"[:120]

    if payload.get("x402Version") != 1:
        return None, "unsupported x402Version"
    if payload.get("scheme") != "exact":
        return None, f"unsupported scheme: {payload.get('scheme')}"
    if payload.get("network") != network:
        return None, f"wrong network: {payload.get('network')}"

    inner = payload.get("payload") or {}
    auth = inner.get("authorization") or {}
    sig = inner.get("signature")
    if not sig:
        return None, "missing signature"

    try:
        frm = str(auth["from"]).lower()
        to = str(auth["to"]).lower()
        value = int(auth["value"])
        valid_after = int(auth["validAfter"])
        valid_before = int(auth["validBefore"])
        nonce = str(auth["nonce"])
    except (KeyError, TypeError, ValueError) as ex:
        return None, f"malformed authorization: {ex}"[:120]

    now = int(time.time())
    if not (valid_after - skew_seconds <= now < valid_before + skew_seconds):
        return None, "authorization outside validity window (expired or not yet valid)"
    if to != pay_to.lower():
        return None, "wrong recipient"
    if value < max_amount_units:
        return None, "insufficient payment amount"

    with _NLOCK:
        _prune()
        if nonce in _USED_NONCES:
            return None, "replayed payment (nonce already used)"

    # REAL crypto verification: EIP-712 recovery against the USDC domain
    domain = USDC_DOMAINS.get(network)
    if domain is None:
        return None, f"unknown network domain: {network}"
    try:
        typed = encode_typed_data(domain_data=domain, message_types=TRANSFER_TYPES,
                                  message_data={"from": frm, "to": to, "value": value,
                                                 "validAfter": valid_after,
                                                 "validBefore": valid_before, "nonce": nonce})
        recovered = Account.recover_message(typed, signature=sig)
    except Exception as ex:
        return None, f"signature verification failed: {ex}"[:120]

    if recovered.lower() != frm:
        return None, "signature does not match authorization 'from'"

    with _NLOCK:
        if nonce in _USED_NONCES:  # double-check under lock
            return None, "replayed payment (nonce already used)"
        _USED_NONCES[nonce] = valid_before + skew_seconds

    return {"from": frm, "value": value, "nonce": nonce, "network": network,
            "payload": payload}, None


def payment_fingerprint(info: dict) -> str:
    """Stable id for ledger/audit linkage (nonce is unique per payment)."""
    return hashlib.sha256(info["nonce"].encode()).hexdigest()[:16]
