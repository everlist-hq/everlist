"""M5: escrow_ref on bookings - optional shape-validated chain pointer.
Proves: paid booking accepts {contract, escrow_id, tx} (echoed verbatim after
validation), visible in /bookings + /orders; every shape wall (exact keys,
positive-int id, bounded strings); WAIVED (free) + escrow_ref -> 409
contradiction; bookings without ref unchanged (backward compat); idempotency
replay keeps the ref.
Run: python test_m5_escrow_ref.py
"""
import hashlib, json, os, socket, subprocess, sys, tempfile, time
import urllib.error, urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
RESULTS = []

def check(name, cond, detail=''):
    RESULTS.append((name, bool(cond)))
    print(('PASS' if cond else 'FAIL'), name, ('' if cond else detail))

def free_port():
    s = socket.socket(); s.bind(('127.0.0.1', 0)); p = s.getsockname()[1]; s.close(); return p

TMP = tempfile.mkdtemp(prefix='hub-m5-')
PORT = free_port(); BASE = 'http://127.0.0.1:%d' % PORT; PYEXE = sys.executable

def req(method, path, body=None, headers=None):
    r = urllib.request.Request(BASE + path, method=method,
        data=json.dumps(body).encode() if body is not None else None,
        headers={'Content-Type': 'application/json', **(headers or {})})
    try:
        with urllib.request.urlopen(r, timeout=10) as resp:
            return resp.status, json.loads(resp.read().decode())
    except urllib.error.HTTPError as e:
        try: return e.code, json.loads(e.read().decode())
        except Exception: return e.code, {}
    except Exception as e: return -1, {'error': str(e)}

def solve_pow(kind):
    _, ch = req('GET', '/auth/challenge?kind=' + kind)
    n = 0
    while True:
        d = hashlib.sha256((ch['challenge'] + str(n)).encode()).digest()
        bits = 0
        for b in d:
            if b == 0: bits += 8; continue
            bits += 8 - b.bit_length(); break
        if bits >= ch['difficulty']: return {'challenge': ch['challenge'], 'nonce': n}
        n += 1

env = {**os.environ, 'HUB_STATE_FILE': os.path.join(TMP, 'state.json'),
       'HUB_POW_SIGNUP_BITS': '8', 'HUB_RATE_BOOKS_PER_MIN': '1000', 'PYTHONUNBUFFERED': '1'}
logf = open(os.path.join(TMP, 'hub.log'), 'a')
proc = subprocess.Popen([PYEXE, os.path.join(HERE, 'app.py'), str(PORT)],
                        stdout=logf, stderr=subprocess.STDOUT, env=env)
end = time.time() + 15; ready = False
while time.time() < end:
    try:
        with socket.create_connection(('127.0.0.1', PORT), timeout=0.5): ready = True; break
    except OSError: time.sleep(0.2)
assert ready, 'test hub did not start'

REF = {'contract': 'midnight1qescrowcontractaddr', 'escrow_id': 7, 'tx': 'a1b2c3d4e5f6a1b2'}
# S1 red-team 3 added the duplicate-ref wall (one chain escrow = one hub booking),
# so the idempotency pair below must use its OWN chain escrow id (REF is already
# claimed by the first booking above; reusing it now correctly yields 409).
REF2 = {'contract': 'midnight1qescrowcontractaddr', 'escrow_id': 8, 'tx': 'b2c3d4e5f6a1b2c3'}

try:
    print('== M5 setup ==')
    c, a = req('POST', '/accounts/signup', {'agent': 'm5-owner', 'pow': solve_pow('signup')})
    assert c == 201, a
    c, l = req('POST', '/accounts/login', {'account_code': a['account_code'], 'agent': 'm5-owner'})
    assert c == 200, l
    OTOK = l['tokens']['list']
    c, r = req('POST', '/listings', {'vertical': 'events', 'title': 'M5 Paid Event',
               'date': '2026-10-10', 'location': 'X', 'price': 5, 'capacity': 10},
               {'X-Hub-Token': OTOK})
    assert c == 201, r
    PAID = r['id']
    c, r = req('POST', '/listings', {'vertical': 'events', 'title': 'M5 Free Event',
               'date': '2026-10-11', 'location': 'X', 'price': 0, 'capacity': 10},
               {'X-Hub-Token': OTOK})
    assert c == 201, r
    FREE = r['id']
    BUYER = 'agent1qm5buyer'
    c, bt = req('POST', '/access', {'agent': BUYER, 'acts': ['book']})
    assert c == 201, bt
    BTOK = bt['tokens']['book']
    B = {'X-Hub-Token': BTOK}

    print('== M5: valid escrow_ref accepted, echoed verbatim ==')
    c, b1 = req('POST', '/book', {'listing_id': PAID, 'attendee': 'M5 Buyer',
                'human_verified': True, 'escrow_ref': REF}, B)
    check('paid booking with escrow_ref -> 201', c == 201, '%s %s' % (c, b1))
    check('escrow_ref echoed verbatim', b1.get('escrow_ref') == REF, str(b1.get('escrow_ref')))
    BID = b1.get('id', '')

    print('== M5: ref visible in buyer view and merchant orders ==')
    c, bl = req('GET', '/bookings', headers=B)
    check('/bookings shows escrow_ref',
          any(b.get('id') == BID and b.get('escrow_ref') == REF for b in bl.get('bookings', [])), str(bl)[:150])
    c, od = req('GET', '/orders', headers={'X-Hub-Token': OTOK})
    check('/orders shows escrow_ref',
          any(o.get('id') == BID and o.get('escrow_ref') == REF for o in od.get('orders', [])), str(od)[:150])

    print('== M5: shape walls ==')
    walls = [
        ('non-dict ref -> 400', 'nope'),
        ('missing key -> 400', {'contract': 'c', 'escrow_id': 1}),
        ('extra key -> 400', {**REF, 'extra': 1}),
        ('empty contract -> 400', {**REF, 'contract': '  '}),
        ('long contract -> 400', {**REF, 'contract': 'x' * 81}),
        ('zero escrow_id -> 400', {**REF, 'escrow_id': 0}),
        ('negative escrow_id -> 400', {**REF, 'escrow_id': -2}),
        ('bool escrow_id -> 400', {**REF, 'escrow_id': True}),
        ('float escrow_id -> 400', {**REF, 'escrow_id': 1.5}),
        ('string escrow_id -> 400', {**REF, 'escrow_id': '7'}),
        ('empty tx -> 400', {**REF, 'tx': ''}),
        ('long tx -> 400', {**REF, 'tx': 'x' * 101}),
        ('non-string tx -> 400', {**REF, 'tx': 123}),
    ]
    for name, bad in walls:
        c, e = req('POST', '/book', {'listing_id': PAID, 'attendee': 'M5 Wall',
                   'human_verified': True, 'escrow_ref': bad}, B)
        check(name, c == 400, '%s %s' % (c, e))

    print('== M5: WAIVED contradiction + backward compat + idempotency ==')
    c, e = req('POST', '/book', {'listing_id': FREE, 'attendee': 'M5 Free',
               'human_verified': True, 'escrow_ref': REF}, B)
    check('free listing + escrow_ref -> 409 contradiction', c == 409, '%s %s' % (c, e))
    c, b2 = req('POST', '/book', {'listing_id': PAID, 'attendee': 'M5 Plain',
                'human_verified': True}, B)
    check('booking without ref unchanged (no key)', c == 201 and 'escrow_ref' not in b2, str(c))
    c, b3 = req('POST', '/book', {'listing_id': PAID, 'attendee': 'M5 Replay',
                'human_verified': True, 'escrow_ref': REF2}, {**B, 'Idempotency-Key': 'm5-key-1'})
    check('idempotent create with ref -> 201', c == 201, str(c))
    c, b3r = req('POST', '/book', {'listing_id': PAID, 'attendee': 'M5 Replay',
                 'human_verified': True, 'escrow_ref': REF2}, {**B, 'Idempotency-Key': 'm5-key-1'})
    ref_out = b3r.get('escrow_ref')
    check('idempotent replay keeps ref', c == 201 and b3r.get('replayed') is True and ref_out == REF2,
          '%s %s' % (c, ref_out))

    print('== M5: OpenAPI contract mentions escrow_ref ==')
    c, oa = req('GET', '/openapi.json')
    note = str(oa.get('paths', {}).get('/book', {}))
    check('openapi /book documents escrow_ref', 'escrow_ref' in note, note[:120])

finally:
    proc.terminate()
    try: proc.wait(timeout=5)
    except Exception: proc.kill()
    logf.close()

fails = [n for n, ok in RESULTS if not ok]
print('')
print('=== m5-escrow-ref: %d/%d passed ===' % (len(RESULTS) - len(fails), len(RESULTS)))
if fails:
    print('FAILED:', fails); sys.exit(1)
print('M5_ESCROW_REF_ALL_PASSED')
