"""M6: Midnight indexer client - read PUBLIC escrow state via GraphQL.

Read-side view for the mirror (M7): the hub NEVER trusts its bookkeeping over
the chain - it reads. Query shape follows the official Midnight Indexer
GraphQL API v4 (ContractAction{state, transaction}; ContractCall adds
entryPoint via inline fragment - the v4 interface-fragment rule).

State decoding: v4 serves serialized contract state as hex. Real-chain decode
needs the ledger-WASM spike (known limitation, recorded). Until then the
ESCROW_STATE_CODEC stand-in (same codec the fixture recorder emits) is:
hex(utf8(JSON {"escrows": {"<id>": {state, amount, booking_ref, deadline}}}))
amount stays a STRING (Uint<128> exceeds int/float guarantees).

Public state only: escrow id, state, amount, booking_ref, deadline. Party
commitments and coin custody stay readable-but-unused; private state lives in
Zswap and is never queried.
"""
import json
import urllib.error
import urllib.request

STATE_NAMES = {1: "HELD", 2: "RELEASED", 3: "REFUNDED"}

# Real v4 shape: connection + edges/node + pageInfo; fragment for entryPoint.
QUERY_ACTIONS = """
query EscrowActions($addr: HexEncoded!) {
  contractActions(address: $addr) {
    edges {
      node {
        __typename
        ... on ContractCall {
          entryPoint
          state
          transaction { id }
        }
      }
    }
    pageInfo { hasNextPage endCursor }
  }
}
"""


class IndexerError(Exception):
    """Honest errors: never guess, never swallow."""

    def __init__(self, message, status=None, detail=None):
        super().__init__(message)
        self.status = status
        self.detail = detail


class EscrowIndexerClient:
    """Read-side client for one escrow contract address."""

    def __init__(self, url, address, timeout=10.0):
        self.url = url
        self.address = address
        self.timeout = timeout

    def _graphql(self, query, variables):
        req = urllib.request.Request(
            self.url, method="POST",
            data=json.dumps({"query": query, "variables": variables}).encode(),
            headers={"Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                return resp.status, json.loads(resp.read().decode())
        except urllib.error.HTTPError as e:
            try:
                body = e.read().decode()[:200]
            except Exception:
                body = ""
            raise IndexerError("indexer returned HTTP %s" % e.code,
                               status=e.code, detail=body) from None
        except urllib.error.URLError as e:
            raise IndexerError("indexer unreachable: %s" % e.reason) from None
        except TimeoutError:
            raise IndexerError("indexer timed out after %ss" % self.timeout) from None

    def actions(self):
        """Contract-call actions at the address, oldest first.
        Returns [{entry_point, tx, state_hex}]."""
        status, body = self._graphql(QUERY_ACTIONS, {"addr": self.address})
        if status != 200:
            raise IndexerError("unexpected indexer status %s" % status, status=status)
        if body.get("errors"):
            raise IndexerError("indexer GraphQL errors: %s"
                               % body["errors"][0].get("message", "?"),
                               detail=str(body["errors"])[:200])
        conn = (body.get("data") or {}).get("contractActions")
        if conn is None:
            raise IndexerError("indexer response missing contractActions")
        out = []
        for edge in conn.get("edges", []):
            node = edge.get("node", {})
            if node.get("__typename") != "ContractCall":
                continue  # deploys/updates carry no escrow transition
            out.append({
                "entry_point": node.get("entryPoint"),
                "tx": (node.get("transaction") or {}).get("id"),
                "state_hex": node.get("state"),
            })
        return out

    @staticmethod
    def decode_state(state_hex):
        """Decode public escrow state (ESCROW_STATE_CODEC; see module doc)."""
        try:
            raw = bytes.fromhex(state_hex)
            doc = json.loads(raw.decode("utf-8"))
        except ValueError:
            raise IndexerError(
                "contract state not decodable with ESCROW_STATE_CODEC "
                "(real chain decode needs the ledger-WASM spike)") from None
        esc = doc.get("escrows")
        if not isinstance(esc, dict):
            raise IndexerError("contract state has no escrows map")
        return esc

    def escrow(self, escrow_id):
        """Public state of one escrow, resolved from the LATEST action.
        Returns {id, state, state_name, amount, booking_ref, deadline, tx,
        entry_point}. Honest errors: not deployed / unknown id / undecodable."""
        acts = self.actions()
        if not acts:
            raise IndexerError("no contract calls at %s (not deployed?)" % self.address)
        last = acts[-1]
        esc = self.decode_state(last["state_hex"])
        key = str(escrow_id)
        if key not in esc:
            raise IndexerError("no escrow #%s in contract state" % escrow_id)
        e = esc[key]
        st = e.get("state")
        if st not in STATE_NAMES:
            raise IndexerError("escrow #%s has unknown state %r" % (escrow_id, st))
        return {
            "id": escrow_id,
            "state": st,
            "state_name": STATE_NAMES[st],
            "amount": e.get("amount"),
            "booking_ref": e.get("booking_ref"),
            "deadline": e.get("deadline"),
            "tx": last["tx"],
            "entry_point": last["entry_point"],
        }
