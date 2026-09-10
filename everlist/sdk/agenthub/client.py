"""AgentHub client — stdlib-only SDK for the Agent Hub Protocol (SPEC v2).

Security posture (SPEC §4/§7):
- credentials travel ONLY in the X-Hub-Token header (never query strings)
- write calls carry an Idempotency-Key (auto-generated uuid4 unless provided)
- bookings() is principal-scoped server-side; the client never widens it
"""
import json
import os
import threading
import time
import urllib.error
import urllib.request
import uuid
from typing import Callable, Optional

from .models import Booking, Listing


class HubError(Exception):
    """Hub returned a non-2xx response. status + parsed error body."""

    def __init__(self, status: int, body: dict):
        self.status = status
        self.body = body
        super().__init__(f"hub {status}: {body.get('error', body)}")


class AgentHub:
    def __init__(self, url: str, timeout: float = 10.0):
        self.url = url.rstrip("/")
        self.timeout = timeout
        self._tokens: dict[str, str] = {}   # act -> token (book/list/confirm/cancel)

    # ---- transport ----
    def _request(self, method: str, path: str, body: Optional[dict] = None,
                 token: Optional[str] = None, idem_key: Optional[str] = None,
                 extra_headers: Optional[dict] = None) -> tuple[int, dict]:
        headers = {"Content-Type": "application/json"}
        if token:
            headers["X-Hub-Token"] = token          # header credentials ONLY
        if idem_key:
            headers["Idempotency-Key"] = idem_key
        if extra_headers:
            headers.update(extra_headers)
        data = json.dumps(body).encode() if body is not None else None
        req = urllib.request.Request(self.url + path, data=data, method=method, headers=headers)
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                return resp.status, json.loads(resp.read().decode())
        except urllib.error.HTTPError as e:
            try:
                payload = json.loads(e.read().decode())
            except Exception:
                payload = {"error": f"HTTP {e.code}"}
            raise HubError(e.code, payload) from None

    @staticmethod
    def _new_idem_key() -> str:
        return uuid.uuid4().hex

    # ---- discovery (public, SPEC §3) ----
    def manifest(self) -> dict:
        _, m = self._request("GET", "/.well-known/agent-hub.json")
        return m

    def list_verticals(self) -> list[str]:
        _, v = self._request("GET", "/verticals")
        return list(v["verticals"].keys())

    def listings(self, vertical: str = "") -> list[Listing]:
        path = "/listings" + (f"?vertical={vertical}" if vertical else "")
        _, r = self._request("GET", path)
        return [Listing.from_api(l) for l in r["listings"]]

    def search(self, q: str) -> list[Listing]:
        from urllib.parse import quote
        _, r = self._request("GET", f"/search?q={quote(q)}")
        return [Listing.from_api(l) for l in r["listings"]]

    def ledger(self) -> dict:
        _, r = self._request("GET", "/ledger")
        return r

    # ---- identity bootstrap (SPEC §6 interim: /access) ----
    def bootstrap(self, agent: str, acts: tuple[str, ...] = ("book", "list")) -> dict:
        """One-time: get capability tokens. In production this is replaced by the
        personhood flow (A2); the SDK shape stays the same."""
        st, r = self._request("POST", "/access", body={"agent": agent, "acts": list(acts)})
        self._tokens.update(r.get("tokens", {}))
        return r

    def _token(self, act: str) -> str:
        tok = self._tokens.get(act)
        if not tok:
            raise HubError(401, {"error": f"no {act} token; call bootstrap() first"})
        return tok

    # ---- merchant side ----
    def add_listing(self, vertical: str, title: str, price: float, capacity: int,
                    idem_key: Optional[str] = None, **fields) -> Listing:
        """Publish a listing. Server owns id/registered/available (SPEC §5)."""
        payload = {"vertical": vertical, "title": title,
                   "price": price, "capacity": capacity, **fields}
        st, r = self._request("POST", "/listings", body=payload,
                              token=self._token("list"),
                              idem_key=idem_key or self._new_idem_key())
        return Listing.from_api({"id": r["id"], "vertical": vertical,
                                 "title": title, "price": price,
                                 "capacity": capacity, "registered": 0,
                                 "available": True})

    # ---- buyer side ----
    def book(self, listing_id: str, quantity: int = 1,
             human_verified: bool = False,
             idem_key: Optional[str] = None, **client_fields) -> Booking:
        """Book with escrow.

        human_verified: asserts the buyer holds a verified-human credential.
        Interim stub: the hub accepts the flag as-is. Production (A2): the
        agent presents a ZK personhood credential generated by the human's
        wallet on-device — the SDK will carry the credential reference, and
        the hub verifies it cryptographically. Never fake this flag in
        production deployments.

        Extra client fields must match the vertical allowlist
        (events: attendee; food: buyer) or the hub rejects with 400.
        """
        payload = {"listing_id": listing_id, "quantity": quantity,
                   "human_verified": human_verified, **client_fields}
        st, r = self._request("POST", "/book", body=payload,
                              token=self._token("book"),
                              idem_key=idem_key or self._new_idem_key())
        return Booking.from_api(r)

    def bookings(self) -> list[Booking]:
        """Principal-scoped: hub returns only bookings made by this client's tokens."""
        _, r = self._request("GET", "/bookings", token=self._token("book"))
        return [Booking.from_api(b) for b in r["bookings"]]

    def orders(self) -> list[Booking]:
        """Merchant view (list token): incoming orders for MY listings.
        Buyers appear as pseudonymous refs; real names never leave the secret store."""
        _, r = self._request("GET", "/orders", token=self._token("list"))
        return [Booking.from_api(b) for b in r["orders"]]

    def private_details(self, booking_id: str, secret: str) -> dict:
        """Private booking details. Credential (the one-time booking secret or a
        details token) goes in the X-Hub-Token header — never in URLs (H1)."""
        _, r = self._request("GET", f"/book/{booking_id}",
                             extra_headers={"X-Hub-Token": secret})
        return r

    def cancel(self, booking_id: str, cancel_token: str) -> dict:
        return self._request("POST", f"/book/{booking_id}/cancel",
                             body={}, token=cancel_token)[1]

    # ---- Tier-1 cryptographic accounts (B5; SPEC §12a transitional) ----
    # crypto imports are LAZY here on purpose: the rest of the SDK stays stdlib-only;
    # keypair accounts need ed25519 (already in requirements.txt via the hub).

    @staticmethod
    def generate_keypair() -> str:
        """Generate an ed25519 seed (64 hex chars). The seed NEVER leaves this
        machine; the hub stores ONLY the derived public key."""
        from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
        return os.urandom(32).hex()

    @staticmethod
    def _pubkey_of(seed_hex: str) -> str:
        from cryptography.hazmat.primitives import serialization
        from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
        if len(seed_hex) != 64:
            raise ValueError("seed must be 64 hex chars")
        sk = Ed25519PrivateKey.from_private_bytes(bytes.fromhex(seed_hex))
        return sk.public_key().public_bytes(
            serialization.Encoding.Raw, serialization.PublicFormat.Raw).hex()

    @staticmethod
    def _solve_pow(challenge: str, difficulty: int) -> dict:
        import hashlib
        n = 0
        while True:
            d = hashlib.sha256((challenge + str(n)).encode()).digest()
            bits = 0
            for b in d:
                if b == 0:
                    bits += 8
                    continue
                bits += 8 - b.bit_length()
                break
            if bits >= difficulty:
                return {"challenge": challenge, "nonce": n}
            n += 1

    def signup_keypair(self, agent: str, seed: Optional[str] = None) -> dict:
        """Tier-1 account signup (keypair). Generates a seed when none is given.
        Returns {'seed', 'pubkey', 'account_id', ...} — the seed is shown ONCE and
        never transmitted; the hub stores only the public key. Logs this client in."""
        seed = seed or self.generate_keypair()
        pub = self._pubkey_of(seed)
        _, ch = self._request("GET", "/auth/challenge?kind=signup")
        pw = self._solve_pow(ch["challenge"], int(ch["difficulty"]))
        st, r = self._request("POST", "/accounts/signup",
                              body={"agent": agent, "pubkey": pub, "pow": pw})
        out = dict(r)
        out["seed"], out["pubkey"] = seed, pub
        self.login_seed(seed, agent)
        return out

    def login_seed(self, seed: str, agent: str) -> dict:
        """Challenge-response login for keypair accounts: the seed itself is never
        sent — the hub proves nothing secret needs to travel. Stores tokens."""
        pub = self._pubkey_of(seed)
        _, ch = self._request("GET", f"/auth/challenge?kind=login&pubkey={pub}")
        from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
        sig = Ed25519PrivateKey.from_private_bytes(bytes.fromhex(seed)).sign(
            b"everlist-login:" + ch["challenge"].encode()).hex()
        st, r = self._request("POST", "/accounts/login",
                              body={"pubkey": pub, "agent": agent, "sig": sig})
        self._tokens.update(r.get("tokens", {}))
        return r

    # ---- watch loop ----
    def on_booking(self, callback: Callable[[Booking], None],
                   poll_seconds: float = 2.0, stop: Optional[threading.Event] = None) -> None:
        """Poll own bookings and invoke callback for each NEW booking id.
        Blocking; pass a threading.Event as `stop` to end from another thread."""
        stop = stop or threading.Event()
        seen: set[str] = set()
        while not stop.is_set():
            try:
                for b in self.bookings():
                    if b.id not in seen:
                        seen.add(b.id)
                        callback(b)
            except HubError as e:
                if e.status == 401:
                    raise  # token revoked/expired: surface to caller
            stop.wait(poll_seconds)
