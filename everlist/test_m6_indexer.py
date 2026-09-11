"""M6: indexer client reads escrow state from an indexer (recorded fixture).
Done-when: reads escrow #1 as HELD, then RELEASED after a simulated
transition; timeouts + honest errors.

The fixture is RECORDED from the real offline contract simulator
(midnight-escrow/contract/dump-fixtures.mjs): funded createEscrow (250 minor
units) -> HELD(1), releaseEscrow -> RELEASED(2). The test serves it in the
official Indexer GraphQL v4 response shape and flips the simulated transition
by extending the action list - exactly how a real indexer would grow history.
Run: python test_m6_indexer.py
"""
import json
import os
import socket
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from midnight_indexer import EscrowIndexerClient, IndexerError  # noqa: E402

RESULTS = []

def check(name, cond, detail=''):
    RESULTS.append((name, bool(cond)))
    print(('PASS' if cond else 'FAIL'), name, ('' if cond else detail))

def free_port():
    s = socket.socket(); s.bind(('127.0.0.1', 0)); p = s.getsockname()[1]; s.close(); return p

# --- recorded fixture (real sim output) ---
FIXTURE = json.load(open(os.path.join(HERE, 'fixtures', 'escrow-fixture.json')))

def state_hex(stage):
    """Encode the recorded public escrow state with ESCROW_STATE_CODEC."""
    doc = {'escrows': {'1': {
        'state': FIXTURE[stage]['state'],
        'amount': FIXTURE[stage]['amount'],
        'booking_ref': FIXTURE[stage]['booking_ref'],
        'deadline': FIXTURE[stage]['deadline'],
    }}}
    return json.dumps(doc).encode('utf-8').hex()

def call_node(stage):
    return {'__typename': 'ContractCall',
            'entryPoint': FIXTURE[stage]['entry_point'],
            'state': state_hex(stage),
            'transaction': {'id': FIXTURE[stage]['tx']}}

class FixtureState:
    released = False          # phase flip: simulated chain transition
    mode = 'ok'               # ok | http500 | gql_error | missing_data

def handle(req_body):
    if FixtureState.mode == 'http500':
        return 500, {'error': 'internal'}
    if FixtureState.mode == 'gql_error':
        return 200, {'errors': [{'message': 'block not found'}]}
    if FixtureState.mode == 'missing_data':
        return 200, {'data': {}}
    addr = (req_body or {}).get('variables', {}).get('addr', '')
    nodes = []
    if addr == ADDR:
        nodes = [call_node('held')]
        if FixtureState.released:
            nodes.append(call_node('released'))
        nodes.insert(0, {'__typename': 'ContractDeploy'})  # must be skipped
    body = {'data': {'contractActions': {
        'edges': [{'node': n} for n in nodes],
        'pageInfo': {'hasNextPage': False, 'endCursor': None}}}}
    return 200, body

class Handler(BaseHTTPRequestHandler):
    def do_POST(self):
        n = int(self.headers.get('Content-Length', 0))
        try:
            req_body = json.loads(self.rfile.read(n).decode())
        except ValueError:
            req_body = {}
        code, body = handle(req_body)
        data = json.dumps(body).encode()
        self.send_response(code)
        self.send_header('Content-Type', 'application/json')
        self.send_header('Content-Length', str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def log_message(self, *a):
        pass

PORT = free_port()
server = ThreadingHTTPServer(('127.0.0.1', PORT), Handler)
threading.Thread(target=server.serve_forever, daemon=True).start()
BASE = 'http://127.0.0.1:%d' % PORT
ADDR = FIXTURE['contract']
cl = EscrowIndexerClient(BASE, ADDR, timeout=5)

try:
    print('== M6: fixture is real recorded sim data ==')
    check('fixture generator recorded', 'dump-fixtures.mjs' in FIXTURE['generator'])
    check('held stage is state 1', FIXTURE['held']['state'] == 1)
    check('released stage is state 2', FIXTURE['released']['state'] == 2)
    ref = bytes.fromhex(FIXTURE['held']['booking_ref']).decode('utf-8').rstrip('\x00')
    check('booking_ref decodes to sim booking', ref == 'bk-m6-fixture', ref)
    check('amount preserved as string 250', FIXTURE['held']['amount'] == '250')

    print('== M6: reads escrow #1 as HELD (phase 1) ==')
    e = cl.escrow(1)
    check('state 1 HELD', e['state'] == 1 and e['state_name'] == 'HELD', str(e))
    check('entry point createEscrow', e['entry_point'] == 'createEscrow')
    check('tx from fixture', e['tx'] == FIXTURE['held']['tx'])
    check('amount readable', e['amount'] == '250')
    check('deadline readable', e['deadline'] == '99999999999')

    print('== M6: simulated transition -> RELEASED (phase 2) ==')
    FixtureState.released = True
    e = cl.escrow(1)
    check('state 2 RELEASED', e['state'] == 2 and e['state_name'] == 'RELEASED', str(e))
    check('entry point releaseEscrow', e['entry_point'] == 'releaseEscrow')
    check('tx follows the chain', e['tx'] == FIXTURE['released']['tx'])

    print('== M6: honest errors ==')
    dead = EscrowIndexerClient('http://127.0.0.1:1', ADDR, timeout=2)
    try:
        dead.escrow(1); check('unreachable -> IndexerError', False)
    except IndexerError as ex:
        check('unreachable -> IndexerError', 'unreachable' in str(ex), str(ex))

    FixtureState.mode = 'http500'
    try:
        cl.escrow(1); check('HTTP 500 -> IndexerError with status', False)
    except IndexerError as ex:
        check('HTTP 500 -> IndexerError with status', ex.status == 500, str(ex))

    FixtureState.mode = 'gql_error'
    try:
        cl.escrow(1); check('GraphQL errors surfaced', False)
    except IndexerError as ex:
        check('GraphQL errors surfaced', 'block not found' in str(ex), str(ex))

    FixtureState.mode = 'missing_data'
    try:
        cl.escrow(1); check('missing data surfaced', False)
    except IndexerError as ex:
        check('missing data surfaced', 'missing contractActions' in str(ex), str(ex))

    FixtureState.mode = 'ok'
    try:
        cl.decode_state('zz-not-hex'); check('bad hex honest error', False)
    except IndexerError as ex:
        check('bad hex honest error', 'not decodable' in str(ex), str(ex))

    import urllib.request
    import urllib.error
    class UndecodableHandler(Handler):
        def do_POST(self):
            data = json.dumps({'data': {'contractActions': {'edges': [
                {'node': {'__typename': 'ContractCall', 'entryPoint': 'x',
                          'state': 'deadbeef', 'transaction': {'id': 't'}}}],
                'pageInfo': {'hasNextPage': False}}}}).encode()
            self.send_response(200)
            self.send_header('Content-Type', 'application/json')
            self.send_header('Content-Length', str(len(data)))
            self.end_headers()
            self.wfile.write(data)
    p2 = free_port()
    s2 = ThreadingHTTPServer(('127.0.0.1', p2), UndecodableHandler)
    threading.Thread(target=s2.serve_forever, daemon=True).start()
    cl2 = EscrowIndexerClient('http://127.0.0.1:%d' % p2, ADDR)
    try:
        cl2.escrow(1); check('undecodable state honest error', False)
    except IndexerError as ex:
        check('undecodable state honest error', 'ledger-WASM spike' in str(ex), str(ex))
    s2.shutdown()

    print('== M6: no escrow / not deployed ==')
    try:
        cl.escrow(99); check('unknown escrow id honest error', False)
    except IndexerError as ex:
        check('unknown escrow id honest error', 'no escrow #99' in str(ex), str(ex))
    cl3 = EscrowIndexerClient(BASE, 'other-address')
    try:
        cl3.escrow(1); check('not deployed honest error', False)
    except IndexerError as ex:
        check('not deployed honest error', 'not deployed' in str(ex), str(ex))

finally:
    server.shutdown()

fails = [n for n, ok in RESULTS if not ok]
print('')
print('=== m6-indexer: %d/%d passed ===' % (len(RESULTS) - len(fails), len(RESULTS)))
if fails:
    print('FAILED:', fails); sys.exit(1)
print('M6_INDEXER_ALL_PASSED')
