# GRIT-spend rails Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ship two reusable EGU drop-ins — a browser-facing node-proxy Flask blueprint and a `signTransaction` method on the onboarding controller — so a consumer can build/sign/submit GRIT support/oppose stakes with the node host kept private.

**Architecture:** `node_proxy_blueprint(make_client, …)` relays a narrow JSON API over an injected `ApiClient`, converting GRIT⇄grains and normalizing subjects/errors; raises an internal `_ProxyError` mapped to JSON by one errorhandler. `signTransaction(unsigned)` mirrors `signLogin`, signing a node-built txn with the unlocked key via `gc-transaction.signUnsignedTxn`.

**Tech Stack:** Flask Blueprint, `httpx`, `Decimal`; vanilla ES modules + `node --test`; pytest.

Spec: `docs/superpowers/specs/2026-06-16-grit-spend-rails-design.md`.

---

## File Structure

- `clients/signing-key/gc-onboarding.mjs` — **modify**: add `signTransaction`.
- `src/gumptionchain/static/signing-key/gc-onboarding.mjs` — **generated** (sync).
- `clients/signing-key/gc-onboarding.test.mjs` — **modify**: signTransaction test.
- `src/gumptionchain/node_proxy.py` — **new**: `node_proxy_blueprint` + helpers.
- `src/gumptionchain/__init__.py` — **modify**: re-export `node_proxy_blueprint`.
- `tests/test_node_proxy.py` — **new**: blueprint tests with a fake client.
- `docs/key-onboarding-for-egu-apps.md` — **modify**: `signTransaction` + proxy note.

---

## Task 1: `signTransaction` on the onboarding controller (JS, TDD)

**Files:**
- Modify: `clients/signing-key/gc-onboarding.mjs`
- Test: `clients/signing-key/gc-onboarding.test.mjs`

- [ ] **Step 1: Write the failing test**

In `clients/signing-key/gc-onboarding.test.mjs`, add these imports after the existing imports at the top (the file already imports `makeOnboarding, NoSigningKeyError, BadPassphraseError` from `./gc-onboarding.mjs` and `verifyMessage` from `./gc-message.mjs`):

```js
import { SigningKey } from './gc-signing-key.mjs';
import { exportEncrypted } from './gc-backup.mjs';
import { txid as txnTxid, signingData } from './gc-transaction.mjs';
```

Then append this test at the end of the file:

```js
test('signTransaction: throws when locked; signs a node-built unsigned txn when unlocked', async () => {
  const onb = makeOnboarding({ store: fakeStore(), window: SECURE });
  await assert.rejects(() => onb.signTransaction({ txid: 'x' }), NoSigningKeyError);

  // Inject a KNOWN key via restore so the test controls address/public_key.
  const k = await SigningKey.generate();
  const backup = await exportEncrypted(k, 'pw');
  await onb.restore({ backup, passphrase: 'pw' });

  // Build a self-consistent unsigned support txn, as the node would return one.
  const base = {
    timestamp: '1700000000',
    address: await k.address(),
    public_key: await k.publicKeyB64(),
    signature: null,
    inflows: [],
    outflows: [{ amount: 100, support: 'Z29ibGlucw' }],
    version: '1',
    prev_hash: null,
  };
  const unsigned = { ...base, txid: await txnTxid(base) };

  const signed = await onb.signTransaction(unsigned);
  assert.equal(signed.address, await k.address());
  assert.equal(signed.public_key, await k.publicKeyB64());
  assert.equal(typeof signed.signature, 'string');
  // The signature is real: it verifies over the canonical signing data.
  assert.equal(await k.verify(signingData(signed), signed.signature), true);
});
```

- [ ] **Step 2: Run test to verify it fails**

Run: `node --test clients/signing-key/gc-onboarding.test.mjs`
Expected: FAIL — `onb.signTransaction is not a function`.

- [ ] **Step 3: Add the method**

In `clients/signing-key/gc-onboarding.mjs`:

(a) Add the import after the existing `import { signMessage } from './gc-message.mjs';` line:

```js
import { signUnsignedTxn } from './gc-transaction.mjs';
```

(b) Add the method just after the existing `signLogin` function:

```js
  async function signTransaction(unsigned) {
    if (!key) {
      throw new NoSigningKeyError('locked: unlock before signing a transaction');
    }
    // signUnsignedTxn recomputes the txid and throws on mismatch, so a
    // dishonest node can't get a signature over fields the user didn't authorize.
    return signUnsignedTxn(unsigned, key);
  }
```

(c) Add `signTransaction` to the returned object. Change:

```js
  return {
    status, onChange, create, unlock, restore, backup, addPasskey,
    signLogin, lock, forget,
  };
```

to:

```js
  return {
    status, onChange, create, unlock, restore, backup, addPasskey,
    signLogin, signTransaction, lock, forget,
  };
```

- [ ] **Step 4: Run test to verify it passes**

Run: `node --test clients/signing-key/gc-onboarding.test.mjs`
Expected: PASS (all tests, including the new one).

- [ ] **Step 5: Vendor to static + parity**

Run: `python3 scripts/sync_signing_key.py && diff clients/signing-key/gc-onboarding.mjs src/gumptionchain/static/signing-key/gc-onboarding.mjs && echo IDENTICAL`
Expected: prints `vendored gc-onboarding.mjs` and `IDENTICAL`.

- [ ] **Step 6: Commit**

```bash
git add clients/signing-key/gc-onboarding.mjs clients/signing-key/gc-onboarding.test.mjs src/gumptionchain/static/signing-key/gc-onboarding.mjs
git commit -m "feat(signing-key): gc-onboarding signTransaction (sign node-built txn)"
```

---

## Task 2: `node_proxy_blueprint` (Python, TDD)

**Files:**
- Create: `src/gumptionchain/node_proxy.py`
- Modify: `src/gumptionchain/__init__.py` (re-export)
- Test: `tests/test_node_proxy.py`

- [ ] **Step 1: Write the failing test**

Create `tests/test_node_proxy.py`:

```python
import json

import httpx
from flask import Flask

from gumptionchain import node_proxy_blueprint


class FakeResponse:
    def __init__(self, status_code, body=None, text=''):
        self.status_code = status_code
        self._body = body
        self.text = text

    def json(self):
        if self._body is None:
            raise ValueError('no json')
        return self._body


class FakeClient:
    """A stand-in ApiClient: each method returns a preset FakeResponse (or
    raises a preset exception). Records calls for assertions."""

    def __init__(self, **responses):
        self._responses = responses
        self.calls = []

    def _resp(self, name, *args, **kwargs):
        self.calls.append((name, args, kwargs))
        r = self._responses[name]
        if isinstance(r, Exception):
            raise r
        return r

    def get_signing_key_balance(self, address, *, raise_for_status=True):
        return self._resp('balance', address)

    def get_support_balance(self, subject, *, raise_for_status=True):
        return self._resp('support', subject)

    def get_opposition_balance(self, subject, *, raise_for_status=True):
        return self._resp('opposition', subject)

    def get_support_transaction(self, pk, amount, subject, *, raise_for_status=True):
        return self._resp('build_support', pk, amount, subject)

    def get_opposition_transaction(self, pk, amount, subject, *, raise_for_status=True):
        return self._resp('build_oppose', pk, amount, subject)

    def get(self, path, *, raise_for_status=True):
        return self._resp('status', path)

    def post(self, path, *, data=None, headers=None, raise_for_status=True):
        return self._resp('submit', path, data)


def _app(client, **bp_kwargs):
    app = Flask(__name__)
    app.register_blueprint(node_proxy_blueprint(lambda: client, **bp_kwargs))
    return app.test_client()


def test_balance_converts_grains_to_grit():
    client = FakeClient(balance=FakeResponse(200, {'balance': 250, 'as_of_block': 'b1'}))
    resp = _app(client).get('/api/node/balance/GCaddrGC')
    assert resp.status_code == 200
    assert resp.get_json() == {'grit': 2.5, 'grains': 250, 'as_of_block': 'b1'}


def test_subject_balances_normalizes_and_converts():
    client = FakeClient(
        support=FakeResponse(200, {'support': 500, 'as_of_block': 'b1'}),
        opposition=FakeResponse(200, {'balance': 300, 'as_of_block': 'b1'}),
    )
    resp = _app(client).get('/api/node/subject/balances?subject=Tabs %3E Spaces')
    assert resp.status_code == 200
    # Proves normalization: support.grains came from the node's "support" key
    # while opposition.grains came from the node's "balance" key (#283).
    assert resp.get_json() == {
        'subject': 'Tabs > Spaces',
        'support': {'grit': 5.0, 'grains': 500},
        'opposition': {'grit': 3.0, 'grains': 300},
    }


def test_subject_balances_rejects_bad_subject():
    client = FakeClient()
    resp = _app(client).get('/api/node/subject/balances?subject=')
    assert resp.status_code == 400
    assert 'subject' in resp.get_json()['error']


def test_build_support_converts_grit_and_passes_raw_subject():
    unsigned = {'txid': 't1', 'outflows': [{'amount': 700, 'support': 'enc'}]}
    client = FakeClient(build_support=FakeResponse(200, unsigned))
    resp = _app(client).post(
        '/api/node/txn/support',
        json={'public_key': 'PUB', 'amount_grit': 7, 'subject': 'goblins'},
    )
    assert resp.status_code == 200
    assert resp.get_json() == unsigned
    # node was called with grains (7 * 100) and the RAW subject
    name, args, _ = client.calls[0]
    assert name == 'build_support'
    assert args == ('PUB', 700, 'goblins')


def test_build_rejects_non_positive_and_sub_grain_amounts():
    client = FakeClient()
    c = _app(client)
    for bad in (0, -5, 0.001, 'x'):
        resp = c.post('/api/node/txn/oppose',
                      json={'public_key': 'P', 'amount_grit': bad, 'subject': 'x'})
        assert resp.status_code == 400, bad


def test_submit_relays_signed_and_returns_txid():
    client = FakeClient(submit=FakeResponse(201, {'received': 't'}))
    signed = {'txid': 'abc123', 'signature': 'SIG', 'outflows': []}
    resp = _app(client).post('/api/node/txn/submit', json={'signed': signed})
    assert resp.status_code == 200
    assert resp.get_json() == {'txid': 'abc123'}
    name, args, _ = client.calls[0]
    assert name == 'submit'
    assert args[0] == '/api/transaction/abc123'


def test_submit_rejects_unsigned_payload():
    client = FakeClient()
    resp = _app(client).post('/api/node/txn/submit', json={'signed': {'txid': 'x'}})
    assert resp.status_code == 400  # missing signature


def test_status_maps_canonical_to_milled():
    client = FakeClient(status=FakeResponse(200, {
        'status': 'canonical', 'block_hash': 'B', 'height': 5, 'confirmations': 3,
    }))
    resp = _app(client).get('/api/node/txn/t1/status')
    assert resp.get_json() == {'state': 'milled', 'block': 'B', 'confirmations': 3}


def test_status_maps_pending_and_orphaned_to_pending():
    for st in ('pending', 'orphaned'):
        client = FakeClient(status=FakeResponse(200, {'status': st, 'block_hash': None}))
        resp = _app(client).get('/api/node/txn/t1/status')
        assert resp.get_json() == {'state': 'pending'}


def test_status_unknown_txid_is_404():
    client = FakeClient(status=FakeResponse(404, {'error': 'not found'}))
    resp = _app(client).get('/api/node/txn/nope/status')
    assert resp.status_code == 404


def test_node_transport_error_is_502():
    client = FakeClient(balance=httpx.ConnectError('down'))
    resp = _app(client).get('/api/node/balance/GCaddrGC')
    assert resp.status_code == 502
    assert resp.get_json()['error'] == 'node unavailable'


def test_node_4xx_is_passed_through_as_400():
    client = FakeClient(build_support=FakeResponse(400, {'error': 'insufficient funds'}))
    resp = _app(client).post('/api/node/txn/support',
                             json={'public_key': 'P', 'amount_grit': 1, 'subject': 'x'})
    assert resp.status_code == 400
    assert resp.get_json()['error'] == 'insufficient funds'


def test_rate_limit_hook_returns_429():
    client = FakeClient(balance=FakeResponse(200, {'balance': 0, 'as_of_block': 'b'}))
    c = _app(client, rate_limit=lambda req: False)
    resp = c.get('/api/node/balance/GCaddrGC')
    assert resp.status_code == 429


def test_oversized_body_is_413():
    client = FakeClient()
    c = _app(client, max_body_bytes=8)
    resp = c.post('/api/node/txn/support',
                  json={'public_key': 'P', 'amount_grit': 1, 'subject': 'x'})
    assert resp.status_code == 413
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_node_proxy.py -q`
Expected: FAIL — `ImportError: cannot import name 'node_proxy_blueprint'`.

- [ ] **Step 3: Write the module**

Create `src/gumptionchain/node_proxy.py`:

```python
from __future__ import annotations

import json
from collections.abc import Callable
from decimal import Decimal, InvalidOperation
from typing import Any

import httpx
from flask import Blueprint, Request, Response, jsonify, request

from gumptionchain.api_client import ApiClient
from gumptionchain.chain import GRAIN_PER_GRIT
from gumptionchain.payload import encode_subject, validate_raw_subject


class _ProxyError(Exception):
    """A browser-facing error: status code + JSON message. Raised by the
    handlers/helpers and mapped to a JSON response by one errorhandler."""

    def __init__(self, status: int, message: str) -> None:
        super().__init__(message)
        self.status = status
        self.message = message


def _node_error(r: Any) -> str:
    try:
        body = r.json()
    except ValueError:
        return r.text or 'node error'
    if isinstance(body, dict) and body.get('error') is not None:
        err = body['error']
        return err if isinstance(err, str) else str(err)
    return r.text or 'node error'


def _call(fn: Callable[..., Any], *args: Any, **kwargs: Any) -> Any:
    """Invoke an ApiClient method with raise_for_status=False; a transport
    failure (node down/unreachable) becomes a 502."""
    try:
        return fn(*args, raise_for_status=False, **kwargs)
    except httpx.RequestError as exc:
        raise _ProxyError(502, 'node unavailable') from exc


def _ok(r: Any) -> Any:
    """Pass a <400 node response through; map node errors to proxy errors."""
    if r.status_code == 404:
        raise _ProxyError(404, _node_error(r))
    if 400 <= r.status_code < 500:
        raise _ProxyError(400, _node_error(r))
    if r.status_code >= 500:
        raise _ProxyError(502, 'node error')
    return r


def _require_subject(subject: object) -> str:
    if not isinstance(subject, str) or not validate_raw_subject(subject):
        raise _ProxyError(400, 'invalid subject (1-79 printable chars)')
    return subject


def _grit_to_grains(value: object) -> int:
    try:
        grit = Decimal(str(value))
    except (InvalidOperation, ValueError):
        raise _ProxyError(400, 'amount_grit must be a number') from None
    if grit <= 0:
        raise _ProxyError(400, 'amount_grit must be positive')
    grains = grit * GRAIN_PER_GRIT
    if grains != grains.to_integral_value():
        raise _ProxyError(400, 'amount_grit precision exceeds 0.01 GRIT')
    return int(grains)


def _grit(grains: int) -> dict[str, Any]:
    return {'grit': grains / GRAIN_PER_GRIT, 'grains': grains}


def node_proxy_blueprint(
    make_client: Callable[[], ApiClient],
    *,
    url_path: str = '/api/node',
    rate_limit: Callable[[Request], bool] | None = None,
    max_body_bytes: int = 65536,
    name: str = 'gumptionchain_node_proxy',
) -> Blueprint:
    """A browser-facing JSON relay over ``ApiClient`` for GRIT support/oppose
    spending, keeping the node host server-side. ``make_client`` supplies a
    configured client (node host + a TRANSACTOR/READER key). The relay holds no
    key; it converts GRIT<->grains, validates subjects, and maps errors."""
    bp = Blueprint(name, __name__, url_prefix=url_path)

    @bp.errorhandler(_ProxyError)
    def _handle(exc: _ProxyError) -> tuple[Response, int]:
        return jsonify({'error': exc.message}), exc.status

    @bp.before_request
    def _guard() -> tuple[Response, int] | None:
        # Return (not raise) to short-circuit — the documented before_request
        # contract — so this never depends on errorhandler timing.
        if rate_limit is not None and not rate_limit(request):
            return jsonify({'error': 'rate limited'}), 429
        length = request.content_length
        if length is not None and length > max_body_bytes:
            return jsonify({'error': 'request too large'}), 413
        return None

    @bp.get('/balance/<address>')
    def balance(address: str) -> Response:
        r = _ok(_call(make_client().get_signing_key_balance, address))
        body = r.json()
        return jsonify({**_grit(int(body['balance'])),
                        'as_of_block': body.get('as_of_block')})

    @bp.get('/subject/balances')
    def subject_balances() -> Response:
        raw = _require_subject(request.args.get('subject'))
        enc = encode_subject(raw)
        client = make_client()
        support = int(_ok(_call(client.get_support_balance, enc)).json()['support'])
        # The node's /opposition endpoint returns grains under "balance" (#283).
        opp = int(_ok(_call(client.get_opposition_balance, enc)).json()['balance'])
        return jsonify({'subject': raw,
                        'support': _grit(support),
                        'opposition': _grit(opp)})

    def _build(method_name: str) -> Response:
        data = request.get_json(silent=True) or {}
        public_key = data.get('public_key')
        if not isinstance(public_key, str) or not public_key:
            raise _ProxyError(400, 'public_key required')
        subject = _require_subject(data.get('subject'))
        grains = _grit_to_grains(data.get('amount_grit'))
        method = getattr(make_client(), method_name)
        return jsonify(_ok(_call(method, public_key, grains, subject)).json())

    @bp.post('/txn/support')
    def txn_support() -> Response:
        return _build('get_support_transaction')

    @bp.post('/txn/oppose')
    def txn_oppose() -> Response:
        return _build('get_opposition_transaction')

    @bp.post('/txn/submit')
    def txn_submit() -> Response:
        data = request.get_json(silent=True) or {}
        signed = data.get('signed')
        if not isinstance(signed, dict):
            raise _ProxyError(400, 'signed txn object required')
        txid = signed.get('txid')
        if not isinstance(txid, str) or not isinstance(signed.get('signature'), str):
            raise _ProxyError(400, 'signed txn must have a txid and signature')
        _ok(_call(make_client().post, f'/api/transaction/{txid}',
                  data=json.dumps(signed),
                  headers={'Content-Type': 'application/json'}))
        return jsonify({'txid': txid})

    @bp.get('/txn/<txid>/status')
    def txn_status(txid: str) -> Response:
        r = _call(make_client().get, f'/api/transaction/{txid}')
        if r.status_code == 404:
            raise _ProxyError(404, 'unknown txid')
        _ok(r)
        body = r.json()
        if body.get('status') == 'canonical':
            return jsonify({'state': 'milled', 'block': body.get('block_hash'),
                            'confirmations': body.get('confirmations')})
        return jsonify({'state': 'pending'})

    return bp
```

- [ ] **Step 4: Re-export from the package root**

In `src/gumptionchain/__init__.py`, add next to the `static_assets_blueprint` re-export:

```python
from gumptionchain.node_proxy import (
    node_proxy_blueprint as node_proxy_blueprint,
)
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `uv run pytest tests/test_node_proxy.py -q`
Expected: PASS (all tests).

- [ ] **Step 6: Lint + type-check**

Run: `uv run ruff check src tests app.py && uv run ruff format --check src tests app.py && uv run mypy`
Expected: clean. If ruff flags a too-many-returns (PLR0911) on `txn_status` or `_ok`, that's acceptable to silence with a `# noqa: PLR0911` and a one-line reason, but first try keeping it under the limit (the structure above is written to). If `ruff format` rewrites anything, run `uv run ruff format src tests app.py` and re-check.

- [ ] **Step 7: Commit**

```bash
git add src/gumptionchain/node_proxy.py src/gumptionchain/__init__.py tests/test_node_proxy.py
git commit -m "feat(proxy): node_proxy_blueprint — browser-facing GRIT-spend relay"
```

---

## Task 3: Docs + full-suite verification

**Files:**
- Modify: `docs/key-onboarding-for-egu-apps.md`

- [ ] **Step 1: Document `signTransaction` + the proxy in the live doc**

In `docs/key-onboarding-for-egu-apps.md`, in the "Minimal use" block (the `## The reusable drop-in (shipped)` section), add the `signTransaction` line right after the `signLogin` line:

```js
    const signed = await onb.signTransaction(unsigned); // sign a node-built txn (GRIT spend)
```

Then, after that code block, add this paragraph:

```markdown
For **spending GRIT** (support/oppose a subject), base also ships a
`node_proxy_blueprint(make_client)` — a browser-facing JSON relay over the node
client that keeps the node host server-side. A consumer mounts it like
`static_assets_blueprint()`:

    from gumptionchain import node_proxy_blueprint
    app.register_blueprint(node_proxy_blueprint(make_client))  # () -> ApiClient

Flow: `GET /api/node/balance/<address>` (confirmed GRIT) → `POST /api/node/txn/support`
or `…/txn/oppose` `{public_key, amount_grit, subject}` → `onb.signTransaction(unsigned)`
→ `POST /api/node/txn/submit {signed}` → poll `GET /api/node/txn/<txid>/status`.
Amounts are whole/2-dp GRIT; the proxy converts to grains.
```

- [ ] **Step 2: Run the full gate suite**

Run each; all must pass:
```bash
node --test clients/signing-key/*.test.mjs src/gumptionchain/static/js/*.test.mjs
uv run pytest -q
uv run ruff check src tests app.py
uv run ruff format --check src tests app.py
uv run mypy
uv run pytest tests/test_signing_key_vendored.py tests/test_no_legacy_key_vocabulary.py -q
```
Expected: JS all pass; pytest all pass; ruff/mypy clean; parity + vocab gates pass.

- [ ] **Step 3: Commit**

```bash
git add docs/key-onboarding-for-egu-apps.md
git commit -m "docs: signTransaction + node_proxy_blueprint (GRIT-spend rails)"
```

---

## After all tasks

Open the PR. **Report back to the gumptactoe session** in the PR body + summary:
- The **proxy factory**: `from gumptionchain import node_proxy_blueprint`, `node_proxy_blueprint(make_client, *, url_path='/api/node', rate_limit=None, max_body_bytes=65536)`, and the **endpoint table** (paths/methods/JSON shapes from the spec).
- **`signTransaction(unsigned)`** → `{ …unsigned, signature, public_key, address }`, served at `/static/gumptionchain/signing-key/gc-onboarding.mjs` (and the `index.mjs` barrel).
- The **GRIT⇄grains convention**: whole/≤2-dp GRIT in; balances out as `{grit, grains}`.
- The **merge commit SHA** to pin.
- Note: the consumer's `make_client` must hold a node signing key with **TRANSACTOR** (build/submit) + **READER** (reads) roles on the node, or the node returns 4xx (passed through). The `/opposition` node key asymmetry is normalized here and tracked for a real fix in #283.
