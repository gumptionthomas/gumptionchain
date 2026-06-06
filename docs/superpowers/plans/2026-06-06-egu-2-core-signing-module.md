# EGU #2.1 — headless gc-sig-v1 core signing module — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A vanilla-ESM, zero-dependency, zero-build JS module under `clients/wallet/` that produces `gc-sig-v1` signatures, `GC`-tagged addresses, and `GC-*` headers byte-for-byte compatible with the Python node, proven by golden vectors and a live `node`→`signing.verify` cross-check, gated in CI via Node's built-in test runner (no npm).

**Architecture:** Three single-responsibility ESM files (`gc-crypto` → `gc-wallet` → `gc-sig`) using only Web Crypto; a Python golden-vector generator/oracle; JS unit tests via `node --test`; a Python live cross-verify; Node 20 added to CI with zero npm packages.

**Tech Stack:** Vanilla JavaScript (ES modules, Web Crypto `crypto.subtle`), Node 20 (built-in `node --test` only — no `package.json`, no `node_modules`), Python 3.12 (test-side generator/parity), pytest, uv, GitHub Actions.

**Spec:** `docs/superpowers/specs/2026-06-06-egu-2-core-signing-module-design.md` (issue #170)

---

## Critical parity facts (verified against the Python code)

- **Sign:** `RSASSA-PKCS1-v1_5` + `SHA-384`, base64 of the raw signature. Deterministic — identical bytes Python/JS for a fixed key+input.
- **Keygen:** RSA modulusLength 2048, publicExponent `0x010001`, hash `SHA-384`.
- **Public key:** DER **SPKI** (`exportKey('spki')`), base64 for `GC-Public-Key` / `public_key_b64`.
- **Private key:** DER **PKCS8** unencrypted (`importKey('pkcs8')`), plain base58 for the b58 form.
- **millHash:** `sha256(sha512(x))` — `subtle.digest('SHA-256', await subtle.digest('SHA-512', x))`.
- **Address:** `'GC' + base58(millHash(spkiDER)) + 'GC'`.
- **base58 is PLAIN (no checksum)** — the `base58check` lib's `b58encode` is plain base58 despite its name. Bitcoin alphabet `123456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz`; big-endian base-256→58; each leading `0x00` byte → one leading `1`. **Do not append a checksum.**
- **Canonical:** UTF-8 `\n`-join of: `gc-sig-v1`, `METHOD.upper()`, `path`, `query`, `sha256(body or b'').hexdigest()`, `node_host`, `timestamp`, `address`.
- **Headers:** `GC-Sig-Version`=`1`, `GC-Address`, `GC-Public-Key` (b64 SPKI), `GC-Timestamp`, `GC-Signature` (b64 sig over canonical).

Golden constants (from the real Python lib, for the JS unit tests):
- `base58(bytes 0x00..0x1f)` = `1thX6LZfHDZZKUs92febYZhYRcXddmzfzF2NvTkPNE`
- `base58(b'hello')` = `Cn8eVZg`
- `millHash('abc')` (hex) = `2b8e2baefea41ddf88d7ccd66550cb9493970ea7854d2e74eb33e57cd3c73d9c`

Node has Web Crypto on `globalThis.crypto` (Node 19+); target Node 20 LTS.

---

## Task 1: `gc-crypto.mjs` primitives + JS unit tests

**Files:**
- Create: `clients/wallet/gc-crypto.mjs`
- Test: `clients/wallet/gc-crypto.test.mjs`

- [ ] **Step 1: Write the failing JS unit test**

Create `clients/wallet/gc-crypto.test.mjs` using Node's built-in runner:

```javascript
import { test } from 'node:test';
import assert from 'node:assert/strict';
import {
  base58encode, base58decode, base64encode, base64decode, millHash,
} from './gc-crypto.mjs';

const hex = (u8) => Buffer.from(u8).toString('hex');
const unhex = (s) => Uint8Array.from(Buffer.from(s, 'hex'));

test('base58 matches the Python base58check lib (plain, no checksum)', () => {
  const bytes0to31 = Uint8Array.from({ length: 32 }, (_, i) => i);
  assert.equal(base58encode(bytes0to31), '1thX6LZfHDZZKUs92febYZhYRcXddmzfzF2NvTkPNE');
  assert.equal(base58encode(new TextEncoder().encode('hello')), 'Cn8eVZg');
});

test('base58 preserves leading zero bytes as leading 1s', () => {
  assert.equal(base58encode(Uint8Array.from([0])), '1');
  assert.equal(base58encode(Uint8Array.from([0, 0, 1])), '112');
});

test('base58 round-trips arbitrary bytes', () => {
  for (const sample of [[0], [255], [0, 0, 7, 200], [1, 2, 3, 4, 5]]) {
    const u8 = Uint8Array.from(sample);
    assert.equal(hex(base58decode(base58encode(u8))), hex(u8));
  }
});

test('base64 round-trips', () => {
  const u8 = Uint8Array.from([0, 1, 250, 99, 7]);
  assert.equal(hex(base64decode(base64encode(u8))), hex(u8));
});

test('millHash is sha256(sha512(x)) — matches Python', async () => {
  const out = await millHash(new TextEncoder().encode('abc'));
  assert.equal(hex(out), '2b8e2baefea41ddf88d7ccd66550cb9493970ea7854d2e74eb33e57cd3c73d9c');
});
```

- [ ] **Step 2: Run, expect FAIL**

Run: `node --test clients/wallet/gc-crypto.test.mjs`
Expected: FAIL — `gc-crypto.mjs` does not exist / exports undefined.

- [ ] **Step 3: Implement `gc-crypto.mjs`**

Create `clients/wallet/gc-crypto.mjs`. Plain base58 (Bitcoin alphabet, leading-zero→`1`, no checksum), base64 via standard btoa/atob-free byte math (works in browser + Node), and `millHash` via Web Crypto.

```javascript
// Pure Web Crypto + vanilla JS. No dependencies. Browser + Node 19+.
const B58 = '123456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz';

export function base58encode(bytes) {
  let zeros = 0;
  while (zeros < bytes.length && bytes[zeros] === 0) zeros++;
  // base-256 -> base-58 (big-endian)
  const digits = [0];
  for (let i = zeros; i < bytes.length; i++) {
    let carry = bytes[i];
    for (let j = 0; j < digits.length; j++) {
      carry += digits[j] << 8;
      digits[j] = carry % 58;
      carry = (carry / 58) | 0;
    }
    while (carry > 0) {
      digits.push(carry % 58);
      carry = (carry / 58) | 0;
    }
  }
  let out = '1'.repeat(zeros);
  for (let k = digits.length - 1; k >= 0; k--) out += B58[digits[k]];
  return out;
}

export function base58decode(str) {
  let zeros = 0;
  while (zeros < str.length && str[zeros] === '1') zeros++;
  const bytes = [0];
  for (let i = zeros; i < str.length; i++) {
    const val = B58.indexOf(str[i]);
    if (val < 0) throw new Error(`invalid base58 char: ${str[i]}`);
    let carry = val;
    for (let j = 0; j < bytes.length; j++) {
      carry += bytes[j] * 58;
      bytes[j] = carry & 0xff;
      carry >>= 8;
    }
    while (carry > 0) {
      bytes.push(carry & 0xff);
      carry >>= 8;
    }
  }
  const out = new Uint8Array(zeros + bytes.length);
  for (let k = 0; k < bytes.length; k++) out[zeros + k] = bytes[bytes.length - 1 - k];
  return out;
}

export function base64encode(bytes) {
  let bin = '';
  for (let i = 0; i < bytes.length; i++) bin += String.fromCharCode(bytes[i]);
  return btoa(bin);
}

export function base64decode(str) {
  const bin = atob(str);
  const out = new Uint8Array(bin.length);
  for (let i = 0; i < bin.length; i++) out[i] = bin.charCodeAt(i);
  return out;
}

export async function millHash(bytes) {
  const inner = await crypto.subtle.digest('SHA-512', bytes);
  const outer = await crypto.subtle.digest('SHA-256', inner);
  return new Uint8Array(outer);
}
```
Note: `btoa`/`atob` exist in browsers and in Node 16+. If the JS test environment lacks them, the implementer may swap to a small manual base64 — but Node 20 has them globally, so prefer the built-ins.

- [ ] **Step 4: Run, expect PASS**

Run: `node --test clients/wallet/gc-crypto.test.mjs` → all pass.

- [ ] **Step 5: Commit**

```bash
git add clients/wallet/gc-crypto.mjs clients/wallet/gc-crypto.test.mjs
git commit -m "$(cat <<'EOF'
feat(wallet): gc-crypto primitives — plain base58, base64, millHash (Web Crypto) (#170)

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>
EOF
)"
```

---

## Task 2: Python golden-vector generator + oracle test

**Files:**
- Create: `tests/test_browser_wallet_vectors.py`
- Create (generated, committed): `clients/wallet/testdata/gc-sig-vectors.json`

- [ ] **Step 1: Generate a fixed test wallet's b58 private key (one-time)**

Run a throwaway to mint a fixed 2048-bit wallet and capture its b58 private key:
```bash
uv run python -c "from gumptionchain.wallet import Wallet; print(Wallet().private_key_b58)"
```
Copy the output; it becomes the `VECTOR_WALLET_B58` constant below (a committed, test-only key, distinct from conftest's canonical wallet).

- [ ] **Step 2: Write the generator + drift-guard/verify test**

Create `tests/test_browser_wallet_vectors.py`. It builds the fixed wallet, emits the vectors (address, public_key_b64, signing cases over representative canonicals), writes them if missing, and asserts the committed file matches a fresh regeneration (drift guard) and that Python verifies every signature.

```python
import json
from pathlib import Path

from gumptionchain.signing import _canonical
from gumptionchain.wallet import Wallet

VECTOR_WALLET_B58 = '<paste from Step 1>'
VECTORS_PATH = (
    Path(__file__).resolve().parent.parent
    / 'clients' / 'wallet' / 'testdata' / 'gc-sig-vectors.json'
)

# Fixed, representative signing cases (cover empty + non-empty body, query).
_CASES = [
    {
        'method': 'GET', 'path': '/api/blocks', 'query': '',
        'body': '', 'node_host': 'node.example', 'timestamp': '1700000000',
    },
    {
        'method': 'POST', 'path': '/api/transactions', 'query': 'foo=bar',
        'body': '{"hello":"world"}', 'node_host': 'node.example',
        'timestamp': '1700000001',
    },
]


def _build_vectors():
    w = Wallet(b58ks=VECTOR_WALLET_B58)
    cases = []
    for c in _CASES:
        canonical = _canonical(
            method=c['method'], path=c['path'], query=c['query'],
            body=c['body'].encode(), node_host=c['node_host'],
            timestamp=c['timestamp'], address=w.address,
        )
        cases.append({
            **c,
            'canonical': canonical.decode(),
            'signature': w.sign(canonical),
        })
    return {
        'private_key_b58': VECTOR_WALLET_B58,
        'public_key_b64': w.public_key_b64,
        'address': w.address,
        'cases': cases,
    }


def test_vectors_committed_and_self_consistent():
    fresh = _build_vectors()
    # Write on first run if absent, so the file is generated deterministically.
    if not VECTORS_PATH.exists():
        VECTORS_PATH.parent.mkdir(parents=True, exist_ok=True)
        VECTORS_PATH.write_text(json.dumps(fresh, indent=2) + '\n')
    committed = json.loads(VECTORS_PATH.read_text())
    assert committed == fresh, 'gc-sig-vectors.json drifted; regenerate'

    # Python verifies every committed signature (sanity that the oracle is real).
    w = Wallet(b58ks=committed['private_key_b58'])
    for case in committed['cases']:
        assert w.validate_signature(case['canonical'].encode(), case['signature'])
```

- [ ] **Step 3: Run to generate + verify**

Run: `uv run pytest tests/test_browser_wallet_vectors.py -q`
Expected: PASS (first run writes the JSON, then asserts equality + verification). Confirm `clients/wallet/testdata/gc-sig-vectors.json` now exists and is committed.

- [ ] **Step 4: Lint/types for the new Python**

Run: `uv run ruff check tests && uv run ruff format --check tests && uv run mypy`
Expected: green (fix any ruff issues in the new test; `VECTOR_WALLET_B58` is a long string — keep within line length or use implicit concatenation).

- [ ] **Step 5: Commit**

```bash
git add tests/test_browser_wallet_vectors.py clients/wallet/testdata/gc-sig-vectors.json
git commit -m "$(cat <<'EOF'
test(wallet): golden gc-sig vectors from a fixed wallet (oracle for JS parity) (#170)

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>
EOF
)"
```

---

## Task 3: `gc-wallet.mjs` + `gc-sig.mjs` + JS tests against the vectors

**Files:**
- Create: `clients/wallet/gc-wallet.mjs`, `clients/wallet/gc-sig.mjs`
- Test: `clients/wallet/gc-wallet.test.mjs`

- [ ] **Step 1: Write the failing JS tests (assert against the golden vectors)**

Create `clients/wallet/gc-wallet.test.mjs`. It loads `testdata/gc-sig-vectors.json`, imports the fixed key, and asserts JS reproduces the committed address, canonical, and signature byte-for-byte, plus a fresh-keygen round-trip and the `GC-*` header shape.

```javascript
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { Wallet } from './gc-wallet.mjs';
import { canonical, signHeaders } from './gc-sig.mjs';

const V = JSON.parse(readFileSync(new URL('./testdata/gc-sig-vectors.json', import.meta.url)));

test('imported fixed key derives the same address + public key as Python', async () => {
  const w = await Wallet.fromPrivateKeyB58(V.private_key_b58);
  assert.equal(await w.address(), V.address);
  assert.equal(await w.publicKeyB64(), V.public_key_b64);
});

test('canonical bytes match Python for every case', async () => {
  const w = await Wallet.fromPrivateKeyB58(V.private_key_b58);
  const addr = await w.address();
  for (const c of V.cases) {
    const bytes = await canonical({
      method: c.method, path: c.path, query: c.query,
      body: new TextEncoder().encode(c.body),
      nodeHost: c.node_host, timestamp: c.timestamp, address: addr,
    });
    assert.equal(new TextDecoder().decode(bytes), c.canonical);
  }
});

test('signatures match Python byte-for-byte (deterministic PKCS1v15)', async () => {
  const w = await Wallet.fromPrivateKeyB58(V.private_key_b58);
  for (const c of V.cases) {
    const sig = await w.sign(new TextEncoder().encode(c.canonical));
    assert.equal(sig, c.signature);
  }
});

test('fresh keygen round-trips and signHeaders has the GC-* shape', async () => {
  const w = await Wallet.generate();
  const headers = await signHeaders(w, {
    method: 'GET', path: '/api/blocks', query: '',
    body: new Uint8Array(), nodeHost: 'node.example', timestamp: '1700000000',
  });
  assert.equal(headers['GC-Sig-Version'], '1');
  assert.equal(headers['GC-Address'], await w.address());
  assert.equal(headers['GC-Public-Key'], await w.publicKeyB64());
  assert.equal(headers['GC-Timestamp'], '1700000000');
  assert.ok(headers['GC-Signature']);
});
```

- [ ] **Step 2: Run, expect FAIL**

Run: `node --test clients/wallet/gc-wallet.test.mjs`
Expected: FAIL — `gc-wallet.mjs` / `gc-sig.mjs` don't exist.

- [ ] **Step 3: Implement `gc-wallet.mjs`**

```javascript
import {
  base58encode, base58decode, base64encode, millHash,
} from './gc-crypto.mjs';

const ALG = { name: 'RSASSA-PKCS1-v1_5' };
const KEYGEN = {
  name: 'RSASSA-PKCS1-v1_5',
  modulusLength: 2048,
  publicExponent: new Uint8Array([0x01, 0x00, 0x01]),
  hash: 'SHA-384',
};
const ADDRESS_TAG = 'GC';

export class Wallet {
  #privateKey; // CryptoKey | null
  #publicKey;  // CryptoKey

  constructor(privateKey, publicKey) {
    this.#privateKey = privateKey;
    this.#publicKey = publicKey;
  }

  static async generate() {
    const pair = await crypto.subtle.generateKey(KEYGEN, true, ['sign', 'verify']);
    return new Wallet(pair.privateKey, pair.publicKey);
  }

  static async fromPrivateKeyB58(b58) {
    const pkcs8 = base58decode(b58);
    const priv = await crypto.subtle.importKey(
      'pkcs8', pkcs8, { name: 'RSASSA-PKCS1-v1_5', hash: 'SHA-384' }, true, ['sign'],
    );
    // Derive the public key from the private (export JWK -> strip private fields).
    const jwk = await crypto.subtle.exportKey('jwk', priv);
    const pubJwk = { kty: jwk.kty, n: jwk.n, e: jwk.e, alg: jwk.alg, ext: true };
    const pub = await crypto.subtle.importKey(
      'jwk', pubJwk, { name: 'RSASSA-PKCS1-v1_5', hash: 'SHA-384' }, true, ['verify'],
    );
    return new Wallet(priv, pub);
  }

  async exportPrivateKeyB58() {
    if (!this.#privateKey) throw new Error('no private key');
    const pkcs8 = new Uint8Array(await crypto.subtle.exportKey('pkcs8', this.#privateKey));
    return base58encode(pkcs8);
  }

  async #spki() {
    return new Uint8Array(await crypto.subtle.exportKey('spki', this.#publicKey));
  }

  async publicKeyB64() {
    return base64encode(await this.#spki());
  }

  async address() {
    const digest = await millHash(await this.#spki());
    return `${ADDRESS_TAG}${base58encode(digest)}${ADDRESS_TAG}`;
  }

  async sign(bytes) {
    if (!this.#privateKey) throw new Error('no private key');
    const sig = await crypto.subtle.sign(ALG, this.#privateKey, bytes);
    return base64encode(new Uint8Array(sig));
  }
}
```
Note on `fromPrivateKeyB58`: deriving the public key from a PKCS8 private key requires the JWK round-trip shown (Web Crypto has no direct "public from private"). If the implementer finds the JWK `alg` field causes an import mismatch, drop `alg` from `pubJwk` (the algorithm is supplied as the import param). The address/publicKeyB64 tests against the vectors will confirm the SPKI matches Python exactly.

- [ ] **Step 4: Implement `gc-sig.mjs`**

```javascript
import { sha256Hex } from './gc-crypto.mjs';

const SIG_SCHEME = 'gc-sig-v1';
const SIG_VERSION = '1';

export async function canonical({ method, path, query, body, nodeHost, timestamp, address }) {
  const bodyDigest = await sha256Hex(body ?? new Uint8Array());
  const lines = [
    SIG_SCHEME, method.toUpperCase(), path, query, bodyDigest,
    nodeHost, timestamp, address,
  ];
  return new TextEncoder().encode(lines.join('\n'));
}

export async function signHeaders(wallet, { method, path, query, body, nodeHost, timestamp }) {
  const address = await wallet.address();
  const bytes = await canonical({ method, path, query, body, nodeHost, timestamp, address });
  return {
    'GC-Sig-Version': SIG_VERSION,
    'GC-Address': address,
    'GC-Public-Key': await wallet.publicKeyB64(),
    'GC-Timestamp': String(timestamp),
    'GC-Signature': await wallet.sign(bytes),
  };
}
```
Add `sha256Hex` to `gc-crypto.mjs` (it was named in the spec; add it now if not already present):
```javascript
export async function sha256Hex(bytes) {
  const d = new Uint8Array(await crypto.subtle.digest('SHA-256', bytes));
  return Array.from(d, (b) => b.toString(16).padStart(2, '0')).join('');
}
```

- [ ] **Step 5: Run, expect PASS**

Run: `node --test clients/wallet/` (runs all `*.test.mjs`)
Expected: all pass — JS reproduces the golden address/canonical/signature byte-for-byte.

- [ ] **Step 6: Commit**

```bash
git add clients/wallet/gc-wallet.mjs clients/wallet/gc-sig.mjs clients/wallet/gc-wallet.test.mjs clients/wallet/gc-crypto.mjs
git commit -m "$(cat <<'EOF'
feat(wallet): gc-wallet + gc-sig — keygen, address, sign, canonical, GC-* headers (#170)

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>
EOF
)"
```

---

## Task 4: Live cross-verify (node ↔ Python) + CI Node 20

**Files:**
- Create: `clients/wallet/sign-cli.mjs` (tiny driver), `tests/test_browser_wallet_parity.py`
- Modify: `.github/workflows/tests.yml`

- [ ] **Step 1: Write the live cross-verify test (failing)**

Add a tiny Node driver `clients/wallet/sign-cli.mjs` that reads a JSON request on argv/stdin, signs with a given b58 key, and prints `{address, signature}`:
```javascript
import { Wallet } from './gc-wallet.mjs';
import { canonical } from './gc-sig.mjs';

const req = JSON.parse(process.argv[2]);
const w = await Wallet.fromPrivateKeyB58(req.private_key_b58);
const address = await w.address();
const bytes = await canonical({
  method: req.method, path: req.path, query: req.query,
  body: new TextEncoder().encode(req.body ?? ''),
  nodeHost: req.node_host, timestamp: req.timestamp, address,
});
const signature = await w.sign(bytes);
process.stdout.write(JSON.stringify({ address, signature }));
```

Create `tests/test_browser_wallet_parity.py` — shells to `node`, feeds the result into the real verifier:
```python
import json
import shutil
import subprocess
from pathlib import Path

import pytest

from gumptionchain.signing import _canonical
from gumptionchain.wallet import Wallet

from tests.test_browser_wallet_vectors import VECTOR_WALLET_B58

CLI = (
    Path(__file__).resolve().parent.parent
    / 'clients' / 'wallet' / 'sign-cli.mjs'
)


@pytest.mark.skipif(shutil.which('node') is None, reason='node not installed')
def test_js_signature_verifies_in_python():
    req = {
        'private_key_b58': VECTOR_WALLET_B58,
        'method': 'POST', 'path': '/api/transactions', 'query': 'a=1',
        'body': '{"x":1}', 'node_host': 'node.example',
        'timestamp': '1700000002',
    }
    out = subprocess.run(
        ['node', str(CLI), json.dumps(req)],
        capture_output=True, text=True, check=True,
    )
    result = json.loads(out.stdout)

    w = Wallet(b58ks=VECTOR_WALLET_B58)
    # Address parity: JS-derived == Python-derived.
    assert result['address'] == w.address
    # Signature parity: the real Python verifier accepts the JS signature.
    canonical = _canonical(
        method=req['method'], path=req['path'], query=req['query'],
        body=req['body'].encode(), node_host=req['node_host'],
        timestamp=req['timestamp'], address=w.address,
    )
    assert w.validate_signature(canonical, result['signature'])
```

- [ ] **Step 2: Run, expect PASS (locally, with node installed)**

Run: `uv run pytest tests/test_browser_wallet_parity.py -q`
Expected: PASS — the JS-produced signature verifies in Python and the address matches. (If `node` is absent the test skips; install Node 20 to actually run it.) This is the load-bearing parity proof.

- [ ] **Step 3: Add Node 20 to CI**

Edit `.github/workflows/tests.yml`. In the test job, add a `setup-node` step (PINNED TO A COMMIT SHA per the repo's actions-pinning policy — look up the current `actions/setup-node` v4 SHA and keep the `# v4.x.x` comment) before the `uv run pytest` step, plus a `node --test` step. Example shape:

```yaml
      - uses: actions/setup-node@<commit-sha>  # v4.x.x
        with:
          node-version: '20'
      - run: node --test clients/wallet/
      # ... existing uv steps; pytest now has node on PATH for the cross-verify
```
Order matters: `setup-node` must come before `uv run pytest` so `node` is on PATH for `test_browser_wallet_parity.py`. Run `node --test clients/wallet/` as its own gating step too. Do NOT add any `package.json`, `npm install`, or `node_modules` — built-in runner only.

- [ ] **Step 4: Validate the workflow locally (syntax) + full local gate**

Run the full local suite to confirm nothing regressed and the parity test passes with node present:
`node --test clients/wallet/ && uv run pytest -q && uv run ruff check src tests && uv run ruff format --check src tests && uv run mypy`
Expected: JS tests pass; full pytest (incl. vectors + parity) green; lint/types green. Eyeball the YAML for valid syntax (indentation, the pinned SHA).

- [ ] **Step 5: Commit**

```bash
git add clients/wallet/sign-cli.mjs tests/test_browser_wallet_parity.py .github/workflows/tests.yml
git commit -m "$(cat <<'EOF'
test(wallet): live node->Python gc-sig cross-verify + Node 20 in CI (no npm) (#170)

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>
EOF
)"
```

---

## Self-review notes

- **Spec coverage:** three ESM files (Tasks 1,3); golden vectors oracle (Task 2); JS unit tests via `node --test` (Tasks 1,3); live cross-verify (Task 4); Node 20 in CI, no npm (Task 4). Storage/passkey/backup/UX/packaging explicitly out of scope.
- **Parity:** base58 is PLAIN (no checksum) — the corrected spec; JS golden constants are real Python outputs; deterministic PKCS1v15 makes signature vectors exact; the live cross-verify is the no-drift proof.
- **Supply chain:** zero npm packages, no `package.json`/`node_modules`; only the Node runtime is added (pinned `setup-node` SHA), per CLAUDE.md.
- **No Python runtime change** — only test-side generator + parity tests; no schema/migration; `db check` unaffected.
- **Type/name consistency:** `Wallet.fromPrivateKeyB58/generate/address/sign/publicKeyB64`, `canonical`, `signHeaders` used consistently across module + tests; `sha256Hex`/`millHash`/`base58encode|decode`/`base64encode|decode` exported from `gc-crypto`.

## Definition of done

- `clients/wallet/{gc-crypto,gc-wallet,gc-sig}.mjs` — vanilla ESM, zero-dep, runnable in browser + Node.
- `node --test clients/wallet/` green; golden vectors committed + drift-guarded; the live `node`→`signing.verify` cross-check passes (JS signature verifies in Python; JS address == Python address).
- Node 20 in CI via a SHA-pinned `setup-node`, `node --test` gating step, node on the pytest PATH; **no npm packages**.
- Full `uv run pytest` + ruff + mypy green; no Python runtime/schema change.
