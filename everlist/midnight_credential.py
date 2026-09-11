"""M14: Midnight credential verifier - Tier-2 sign-in for the hub.

Verifies that a credential holder is ADMITTED and NOT-REVOKED on the
credential contract (M13: experiments/midnight-escrow, credential.compact),
so the hub can set verified_by: midnight-zk server-side. Mirrors
midnight_indexer.py (M6): honest errors, public state only, chain as source
of truth, and an explicit MODE on every result - a missing verifier is
BLOCKED, never a silent success (master plan capability modes).

Modes:
  chain     - live GraphQL indexer (official v4 shape); real contract state
  simulated - recorded action timeline from the REAL offline circuit
              simulator (fixtures/credential-timeline.json; regenerated live
              in tests when node + contract deps exist)

State codec (agreed with the M13 scenario driver):
  state_hex = hex(utf8(JSON {"holders": {"<id>": "<commitment-hex>"},
                              "revoked": {"<id>": true|false}}))

What is verified (the done-when semantics):
  admitted    - credential id exists in the holders map
  not revoked - revoked map marks it false
  (proving the CALLER holds the secret behind the commitment is the wallet's
   ZK proveHolder act, captured in the timeline as a successful proveHolder
   call; the hub-side check is the chain-state read that the credential is
   still valid at sign-in time - revocation applies immediately.)
"""
import binascii
import json
import os
import urllib.error
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
FIXTURE_PATH = os.path.join(HERE, "fixtures", "credential-timeline.json")

# Official v4 indexer shape (same wire rule as M6: connection + edges/node;
# inline fragment picks ContractCall's entryPoint out of the interface).
QUERY_CRED_ACTIONS = """
query CredActions($addr: HexEncoded!) {
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


class CredentialError(Exception):
    """Honest errors: never guess, never swallow."""

    def __init__(self, message, status=None, detail=None):
        super().__init__(message)
        self.status = status
        self.detail = detail


def decode_state(state_hex):
    """Decode one contract-state snapshot (codec above); honest failures."""
    try:
        raw = binascii.unhexlify(state_hex)
    except (binascii.Error, ValueError):
        raise CredentialError("state_hex is not valid hex") from None
    try:
        obj = json.loads(raw.decode("utf-8"))
    except Exception:
        raise CredentialError("state_hex does not decode to the credential JSON codec") from None
    if not isinstance(obj, dict) or "holders" not in obj or "revoked" not in obj:
        raise CredentialError("credential state missing holders/revoked maps")
    return obj


class CredentialVerifier:
    """Read-side verifier for one credential contract address."""

    def __init__(self, address, url=None, fixture_path=FIXTURE_PATH, timeout=10.0):
        self.address = address
        self.url = url            # chain mode when set
        self.fixture_path = fixture_path
        self.timeout = timeout

    # ---- transport helpers ------------------------------------------------
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
            raise CredentialError("indexer returned HTTP %s" % e.code,
                                  status=e.code, detail=body) from None
        except urllib.error.URLError as e:
            raise CredentialError("indexer unreachable: %s" % e.reason) from None
        except TimeoutError:
            raise CredentialError("indexer timed out after %ss" % self.timeout) from None

    def _actions_chain(self):
        status, body = self._graphql(QUERY_CRED_ACTIONS, {"addr": self.address})
        if status != 200:
            raise CredentialError("unexpected indexer status %s" % status, status=status)
        if body.get("errors"):
            raise CredentialError("indexer GraphQL errors: %s"
                                  % body["errors"][0].get("message", "?"))
        conn = (body.get("data") or {}).get("contractActions")
        if conn is None:
            raise CredentialError("indexer response missing contractActions")
        out = []
        for edge in conn.get("edges", []):
            node = edge.get("node", {})
            if node.get("__typename") != "ContractCall":
                continue  # deploys/updates carry no credential transition
            out.append({"entry_point": node.get("entryPoint"),
                        "tx": (node.get("transaction") or {}).get("id"),
                        "state_hex": node.get("state")})
        return out

    def _actions_fixture(self):
        """Simulated mode: the recorded timeline of the REAL offline circuit
        simulator (label surfaces in every result)."""
        try:
            doc = json.load(open(self.fixture_path))
        except FileNotFoundError:
            raise CredentialError("credential timeline fixture not found: %s"
                                  % self.fixture_path) from None
        except (json.JSONDecodeError, ValueError) as e:
            # fail-closed: ANY unparsable evidence (bad json, binary garbage,
            # invalid utf-8) is an honest error - never a crash, never a pass
            raise CredentialError("credential fixture unparsable: %s" % e) from None
        if doc.get("format") != "everlist-credential-timeline/1":
            raise CredentialError("fixture format unknown: %r" % doc.get("format"))
        out = []
        for a in doc.get("actions", []):
            out.append({"entry_point": a.get("entry_point"),
                        "tx": a.get("tx"), "state_hex": a.get("state_hex")})
        return out

    def actions(self):
        if self.url:
            return self._actions_chain()
        return self._actions_fixture()

    def mode(self):
        return "chain" if self.url else "simulated"

    # ---- the verification the hub performs -------------------------------
    def verify(self, credential_id, holder_commitment_hex=None):
        """Verify credential_id is ADMITTED + NOT-REVOKED in the contract's
        latest public state. Returns {mode, credential_id, admitted, revoked,
        verified_by, evidence_tx} - fail-closed on every error path."""
        cid = str(credential_id)
        acts = self.actions()
        if not acts:
            raise CredentialError("no credential contract actions found")
        latest = decode_state(acts[-1]["state_hex"])
        holders = latest.get("holders") or {}
        revoked = latest.get("revoked") or {}
        evidence_tx = acts[-1].get("tx")
        if cid not in holders:
            return {"mode": self.mode(), "credential_id": cid, "admitted": False,
                    "revoked": None, "verified_by": None,
                    "evidence_tx": evidence_tx}
        is_revoked = bool(revoked.get(cid, True))  # fail-closed: unmarked = revoked
        if holder_commitment_hex is not None:
            if holders[cid].lower() != holder_commitment_hex.lower():
                raise CredentialError("credential %s bound to a different holder commitment" % cid)
        return {"mode": self.mode(), "credential_id": cid,
                "admitted": True, "revoked": is_revoked,
                "verified_by": ("midnight-zk" if not is_revoked else None),
                "evidence_tx": evidence_tx}
