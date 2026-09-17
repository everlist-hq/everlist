import json
import time
import urllib.request
import urllib.error
import uuid

# Phase D (owner-approved 2026-09-17): MCP face over the hub's own REST API.
# Hand-rolled Model Context Protocol (streamable-HTTP shape, JSON-RPC 2.0).
# Why no SDK: the box runs system Python with zero extra deps; the protocol
# surface we need (initialize / tools/list / tools/call / ping) is small and
# stable. Why loopback: EVERY tool call is forwarded to this same process's
# real REST endpoint, so all hub laws apply unchanged - auth (X-Hub-Token),
# per-source rate limits, validation, idempotency, escrow accounting. The MCP
# layer owns NO logic of its own; it is a translator, nothing more.

PROTOCOL_VERSION = '2025-03-26'
SUPPORTED = {'2025-03-26', '2024-11-05'}
SERVER_INFO = {'name': 'everlist-hub', 'version': 'agent-hub/0.2-mcp'}

TOOLS = [
    {
        'name': 'everlist_contract',
        'description': ('Discovery: the EverList hub manifest - payment mode, fee policy, '
                        'identity requirements, verticals, and endpoint map. Call this first.'),
        'inputSchema': {'type': 'object', 'properties': {}},
    },
    {
        'name': 'everlist_verticals',
        'description': 'Vertical schema registry: required/optional fields, categories, and booking shape per vertical (events, food, services, jobs, classes, p2p).',
        'inputSchema': {'type': 'object', 'properties': {}},
    },
    {
        'name': 'everlist_search',
        'description': ('Search public listings. q = free text; optional vertical, category, '
                        'tags (comma-separated), from/to (YYYY-MM-DD), min_price/max_price (numbers), sort.'),
        'inputSchema': {
            'type': 'object',
            'properties': {
                'q': {'type': 'string', 'description': 'free-text query'},
                'vertical': {'type': 'string', 'description': 'events|food|services|jobs|classes|p2p'},
                'category': {'type': 'string'},
                'tags': {'type': 'string', 'description': 'comma-separated tags'},
                'from': {'type': 'string', 'description': 'YYYY-MM-DD'},
                'to': {'type': 'string', 'description': 'YYYY-MM-DD'},
                'min_price': {'type': 'number'},
                'max_price': {'type': 'number'},
                'sort': {'type': 'string'},
            },
        },
    },
    {
        'name': 'everlist_listing',
        'description': 'One listing, full record: title, date, place, price, escrow terms, capacity, rating.',
        'inputSchema': {
            'type': 'object',
            'required': ['id'],
            'properties': {'id': {'type': 'string', 'description': 'listing id, e.g. even-1 or jobs-1'}},
        },
    },
    {
        'name': 'everlist_book',
        'description': ('Book (or apply+pay for jobs) a listing into escrow. Requires a hub token '
                        '(X-Hub-Token header on the MCP request, or token argument - obtain via '
                        'the hub auth contract: POST /accounts/signup + /accounts/login, keypair '
                        'stays client-side). human_verified must be true (interim credential; '
                        'production = zk-personhood). Booking fields depend on the vertical '
                        '(attendee / buyer+quantity / client / worker).'),
        'inputSchema': {
            'type': 'object',
            'required': ['listing_id'],
            'properties': {
                'listing_id': {'type': 'string'},
                'quantity': {'type': 'integer', 'minimum': 1},
                'human_verified': {'type': 'boolean'},
                'attendee': {'type': 'string'},
                'buyer': {'type': 'string'},
                'client': {'type': 'string'},
                'worker': {'type': 'string'},
                'token': {'type': 'string', 'description': 'hub token if no X-Hub-Token header'},
                'idempotency_key': {'type': 'string'},
            },
        },
    },
    {
        'name': 'everlist_booking',
        'description': 'Booking status for the token holder: escrow state, amount, confirmation code.',
        'inputSchema': {
            'type': 'object',
            'required': ['booking_id'],
            'properties': {
                'booking_id': {'type': 'string'},
                'token': {'type': 'string', 'description': 'hub token if no X-Hub-Token header'},
            },
        },
    },
]


class MCPError(Exception):
    def __init__(self, code, message, rid=None):
        super().__init__(message)
        self.code = code
        self.message = message
        self.rid = rid


def _api(hub_port, method, path, payload=None, token=None, idem=None, timeout=10):
    headers = {'Content-Type': 'application/json'}
    if token:
        headers['X-Hub-Token'] = token
    if idem:
        headers['Idempotency-Key'] = idem
    r = urllib.request.Request(
        'http://127.0.0.1:%d%s' % (hub_port, path),
        data=json.dumps(payload).encode() if payload is not None else None,
        headers=headers, method=method)
    try:
        with urllib.request.urlopen(r, timeout=timeout) as resp:
            return resp.status, json.loads(resp.read().decode())
    except urllib.error.HTTPError as e:
        try:
            return e.code, json.loads(e.read().decode() or '{}')
        except Exception:
            return e.code, {}


def _rpc(result, rid):
    return {'jsonrpc': '2.0', 'id': rid, 'result': result}


def _rpc_err(code, message, rid):
    return {'jsonrpc': '2.0', 'id': rid, 'error': {'code': code, 'message': message}}


def _handle_one(handler, data, hub_port):
    if not isinstance(data, dict) or data.get('jsonrpc') != '2.0':
        return _rpc_err(-32600, 'not a JSON-RPC 2.0 request', data.get('id') if isinstance(data, dict) else None), None
    method = data.get('method', '')
    rid = data.get('id')
    params = data.get('params') or {}

    if method == 'initialize':
        ver = params.get('protocolVersion')
        use = ver if ver in SUPPORTED else PROTOCOL_VERSION
        result = {
            'protocolVersion': use,
            'capabilities': {'tools': {}},
            'serverInfo': SERVER_INFO,
            'instructions': (
                'EverList: a listing = something with a date where a human is needed or wanted. '
                'Call everlist_contract first (payment mode + fees are declared, never hidden). '
                'Search, inspect, then book with everlist_book; escrow holds the funds until '
                'the organizer confirms or the refund window lapses.'),
        }
        handler._mcp_session = getattr(handler, '_mcp_session', None) or ('mcp-' + uuid.uuid4().hex)
        return _rpc(result, rid), {'Mcp-Session-Id': handler._mcp_session}

    if method == 'notifications/initialized':
        return None, None  # notification: no response

    if method == 'ping':
        return _rpc({}, rid), None

    if method == 'tools/list':
        return _rpc({'tools': TOOLS}, rid), None

    if method == 'tools/call':
        name = params.get('name', '')
        args = params.get('arguments') or {}
        try:
            result = _call_tool(handler, hub_port, name, args)
            return _rpc({'content': [{'type': 'text', 'text': json.dumps(result, ensure_ascii=False)}],
                         'isError': bool(result.get('isError'))}, rid), None
        except MCPError as e:
            return _rpc({'content': [{'type': 'text', 'text': e.message}], 'isError': True}, rid), None
        except Exception as e:  # never leak a traceback to the wire
            return _rpc({'content': [{'type': 'text', 'text': 'internal error: %s' % type(e).__name__}],
                         'isError': True}, rid), None

    if rid is None:
        return None, None  # unknown notification: ignore per spec
    return _rpc_err(-32601, 'unknown method: %s' % method, rid), None


def _call_tool(handler, hub_port, name, args):
    if name == 'everlist_contract':
        st, m = _api(hub_port, 'GET', '/.well-known/agent-hub.json')
        if st != 200:
            raise MCPError(-32000, 'contract unavailable (HTTP %s)' % st)
        return m
    if name == 'everlist_verticals':
        st, v = _api(hub_port, 'GET', '/verticals')
        if st != 200:
            raise MCPError(-32000, 'verticals unavailable (HTTP %s)' % st)
        return v
    if name == 'everlist_search':
        qs = '&'.join('%s=%s' % (k, urllib.request.quote(str(v), safe=''))
                      for k, v in args.items()
                      if k in ('q', 'vertical', 'category', 'tags', 'from', 'to',
                               'min_price', 'max_price', 'sort') and v not in (None, ''))
        st, res = _api(hub_port, 'GET', '/search' + (('?' + qs) if qs else ''))
        if st != 200:
            raise MCPError(-32000, 'search failed (HTTP %s): %s' % (st, res.get('error', '')))
        return res
    if name == 'everlist_listing':
        lid = str(args.get('id', '')).strip()
        if not lid or '/' in lid or '..' in lid:
            raise MCPError(-32602, 'invalid listing id')
        st, res = _api(hub_port, 'GET', '/listings/' + lid)
        if st == 404:
            raise MCPError(-32000, 'no such listing: %s' % lid)
        if st == 410:
            raise MCPError(-32000, 'listing archived: %s' % lid)
        if st != 200:
            raise MCPError(-32000, 'fetch failed (HTTP %s)' % st)
        return res
    if name == 'everlist_book':
        token = args.get('token') or handler.headers.get('X-Hub-Token', '')
        if not token:
            raise MCPError(-32001, 'missing hub token: pass X-Hub-Token header or token argument (see everlist_contract auth)')
        payload = {'listing_id': args.get('listing_id'), 'human_verified': bool(args.get('human_verified'))}
        if args.get('quantity') is not None:
            payload['quantity'] = args.get('quantity')
        for f in ('attendee', 'buyer', 'client', 'worker'):
            if args.get(f):
                payload[f] = args[f]
        idem = args.get('idempotency_key') or ('mcp-' + uuid.uuid4().hex)
        st, res = _api(hub_port, 'POST', '/book', payload, token=token, idem=idem)
        if st in (200, 201):
            return res
        return {'isError': True, 'http_status': st, 'error': res.get('error', 'booking failed'),
                'hint': res.get('hint', res.get('note', ''))}
    if name == 'everlist_booking':
        token = args.get('token') or handler.headers.get('X-Hub-Token', '')
        if not token:
            raise MCPError(-32001, 'missing hub token: pass X-Hub-Token header or token argument')
        bid = str(args.get('booking_id', '')).strip()
        if not bid or '/' in bid or '..' in bid:
            raise MCPError(-32602, 'invalid booking id')
        st, res = _api(hub_port, 'GET', '/bookings/' + bid, token=token)
        if st != 200:
            raise MCPError(-32000, 'booking lookup failed (HTTP %s): %s' % (st, res.get('error', '')))
        return res
    raise MCPError(-32602, 'unknown tool: %s' % name)


def handle(handler, data, hub_port):
    """Entry point from app.py do_POST. Returns (status, body, extra_headers)."""
    t0 = time.time()
    if isinstance(data, list):  # JSON-RPC batching
        out = []
        for item in data:
            try:
                resp, hdrs = _handle_one(handler, item, hub_port)
                if resp is not None:
                    out.append(resp)
            except Exception:
                out.append(_rpc_err(-32603, 'internal error', item.get('id') if isinstance(item, dict) else None))
        if not out:
            return 202, {}, {}
        return 200, out, {}
    try:
        resp, hdrs = _handle_one(handler, data, hub_port)
    except Exception:
        rid = data.get('id') if isinstance(data, dict) else None
        resp, hdrs = _rpc_err(-32603, 'internal error', rid), None
    if resp is None:
        return 202, {}, (hdrs or {})
    return 200, resp, (hdrs or {})
