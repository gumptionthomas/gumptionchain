# gc-onboarding Reusable Controller Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ship a headless, style-agnostic signing-key onboarding controller (`gc-onboarding.mjs`) plus a Python static-mount helper so any EGU app adopts the create → back up → restore → unlock → sign-login flow by inclusion.

**Architecture:** A pure ES module orchestrates the existing low-level `gc-*` modules over a single in-memory unlocked-key holder; the app owns all DOM and renders from `status()` + `onChange()`. A static-only Flask blueprint serves base's assets to consumers that don't register the chain explorer.

**Tech Stack:** Vanilla ES modules (no bundler), `node --test`; Flask Blueprint + `importlib.resources`; pytest.

Spec: `docs/superpowers/specs/2026-06-15-gc-onboarding-reusable-controller-design.md`.

---

## File Structure

- `src/gumptionchain/static_assets.py` — **new**: the `static_assets_blueprint()` helper.
- `src/gumptionchain/__init__.py` — **modify**: re-export `static_assets_blueprint`.
- `tests/test_static_assets.py` — **new**: pytest for the helper.
- `clients/signing-key/gc-onboarding.mjs` — **new**: the controller (source of truth).
- `clients/signing-key/gc-onboarding.test.mjs` — **new**: node tests.
- `src/gumptionchain/static/signing-key/gc-onboarding.mjs` — **generated** by `scripts/sync_signing_key.py` (do not hand-edit).
- `docs/key-onboarding-for-egu-apps.md` — **modify**: flip "Recommended follow-up" → Shipped.

---

## Task 1: Python static-mount helper

**Files:**
- Create: `src/gumptionchain/static_assets.py`
- Modify: `src/gumptionchain/__init__.py` (add a re-export with the other top-level imports)
- Test: `tests/test_static_assets.py`

- [ ] **Step 1: Write the failing test**

Create `tests/test_static_assets.py`:

```python
from flask import Flask

from gumptionchain import static_assets_blueprint


def test_static_assets_blueprint_serves_a_signing_key_module():
    # A consumer that embeds the package but does NOT register the full
    # browser blueprint can still serve base's ESM modules.
    app = Flask(__name__)
    app.register_blueprint(static_assets_blueprint())
    client = app.test_client()

    resp = client.get('/static/gumptionchain/signing-key/gc-keyring.mjs')
    assert resp.status_code == 200
    assert b'export' in resp.data  # it served the real module, not a 404 page


def test_static_assets_blueprint_url_path_is_overridable():
    app = Flask(__name__)
    app.register_blueprint(static_assets_blueprint(url_path='/assets/gc'))
    client = app.test_client()

    assert client.get('/assets/gc/signing-key/gc-keyring.mjs').status_code == 200
    assert client.get('/static/gumptionchain/signing-key/gc-keyring.mjs').status_code == 404
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_static_assets.py -q`
Expected: FAIL with `ImportError: cannot import name 'static_assets_blueprint' from 'gumptionchain'`.

- [ ] **Step 3: Write the helper**

Create `src/gumptionchain/static_assets.py`:

```python
from __future__ import annotations

from importlib.resources import files

from flask import Blueprint


def static_assets_blueprint(
    url_path: str = '/static/gumptionchain',
    name: str = 'gumptionchain_static',
) -> Blueprint:
    """A static-only blueprint serving base's browser assets (the signing-key
    ESM modules + JS glue) for consumers that embed the ``gumptionchain``
    package but do NOT register the full ``browser`` blueprint (chain explorer
    + DB).

    The default ``url_path`` matches the ``browser`` blueprint's, so module
    URLs are identical whether a consumer mounts the explorer or only assets.
    """
    static_folder = str(files('gumptionchain') / 'static')
    return Blueprint(
        name,
        __name__,
        static_folder=static_folder,
        static_url_path=url_path,
    )
```

- [ ] **Step 4: Re-export from the package root**

In `src/gumptionchain/__init__.py`, add this import alongside the other top-level imports (just after `from flask_migrate import Migrate`):

```python
from gumptionchain.static_assets import static_assets_blueprint
```

If Ruff flags the import as unused (F401), add it to `__all__` or annotate it; simplest is to mark it re-exported:

```python
from gumptionchain.static_assets import static_assets_blueprint as static_assets_blueprint
```

- [ ] **Step 5: Run test to verify it passes**

Run: `uv run pytest tests/test_static_assets.py -q`
Expected: PASS (2 passed).

- [ ] **Step 6: Lint + type-check**

Run: `uv run ruff check src tests app.py && uv run ruff format --check src tests app.py && uv run mypy`
Expected: all clean.

- [ ] **Step 7: Commit**

```bash
git add src/gumptionchain/static_assets.py src/gumptionchain/__init__.py tests/test_static_assets.py
git commit -m "feat(static): static_assets_blueprint for non-node consumers"
```

---

## Task 2: The `gc-onboarding.mjs` controller (TDD)

**Files:**
- Create: `clients/signing-key/gc-onboarding.mjs`
- Test: `clients/signing-key/gc-onboarding.test.mjs`

- [ ] **Step 1: Write the failing test**

Create `clients/signing-key/gc-onboarding.test.mjs`:

```js
import { test } from 'node:test';
import assert from 'node:assert/strict';

import { makeOnboarding, NoSigningKeyError } from './gc-onboarding.mjs';
import { verifyMessage } from './gc-message.mjs';

function fakeStore() {
  let rec = null;
  return {
    get: async () => rec,
    put: async (r) => { rec = r; },
    delete: async () => { rec = null; },
  };
}

function fakePasskey(fill = 7, credentialId = 'cred1') {
  const PRF = new Uint8Array(32).fill(fill);
  return {
    isSupported: async () => true,
    enroll: async () => ({ credentialId, prfOutput: PRF }),
    unlock: async () => PRF,
  };
}

const SECURE = { isSecureContext: true };

test('empty store: status reports no key, passkey off without an adapter', async () => {
  const onb = makeOnboarding({ store: fakeStore(), window: SECURE });
  const s = await onb.status();
  assert.equal(s.hasKey, false);
  assert.equal(s.unlocked, false);
  assert.equal(s.address, null);
  assert.equal(s.passkeySupported, false);
  assert.equal(s.secureContext, true);
});

test('create persists + holds unlocked, and onChange fires with fresh status', async () => {
  const onb = makeOnboarding({ store: fakeStore(), window: SECURE });
  let last = null;
  const off = onb.onChange((s) => { last = s; });
  const { address } = await onb.create({ passphrase: 'pw' });
  assert.match(address, /^GC.*GC$/);
  const s = await onb.status();
  assert.equal(s.hasKey, true);
  assert.equal(s.unlocked, true);
  assert.equal(s.address, address);
  assert.equal(last.unlocked, true);
  off();
});

test('lock drops the in-memory key, keeps the record; address still readable', async () => {
  const onb = makeOnboarding({ store: fakeStore(), window: SECURE });
  const { address } = await onb.create({ passphrase: 'pw' });
  await onb.lock();
  const s = await onb.status();
  assert.equal(s.unlocked, false);
  assert.equal(s.hasKey, true);
  assert.equal(s.address, address); // welcome-back address from the record
});

test('unlock by passphrase re-holds the key; wrong passphrase rejects', async () => {
  const onb = makeOnboarding({ store: fakeStore(), window: SECURE });
  const { address } = await onb.create({ passphrase: 'pw' });
  await onb.lock();
  const r = await onb.unlock({ passphrase: 'pw' });
  assert.equal(r.address, address);
  assert.equal((await onb.status()).unlocked, true);
  await onb.lock();
  await assert.rejects(() => onb.unlock({ passphrase: 'WRONG' }));
});

test('passkey: create with passkey, unlock by passkey', async () => {
  const onb = makeOnboarding({ store: fakeStore(), window: SECURE, passkey: fakePasskey() });
  assert.equal((await onb.status()).passkeySupported, true);
  const { address } = await onb.create({ passphrase: 'pw', withPasskey: true });
  await onb.lock();
  const r = await onb.unlock({ passkey: true });
  assert.equal(r.address, address);
});

test('backup yields an encrypted artifact (no raw key) + filename; restore into a fresh store recovers the same address', async () => {
  const onb1 = makeOnboarding({ store: fakeStore(), window: SECURE });
  const { address } = await onb1.create({ passphrase: 'pw' });
  const { artifact, filename } = await onb1.backup({ passphrase: 'pw' });
  assert.equal(artifact.kind, 'gc-signing-key-backup');
  assert.match(filename, /^gc-signing-key-backup-.*\.json$/);
  // The artifact is encrypted: no private-key field, only the sealed envelope.
  assert.deepEqual(
    Object.keys(artifact).sort(),
    ['address', 'ciphertext', 'iv', 'kdf', 'kind', 'version'],
  );

  // A different origin/store restores from the artifact (string form too).
  const onb2 = makeOnboarding({ store: fakeStore(), window: SECURE });
  const r = await onb2.restore({ backup: JSON.stringify(artifact), passphrase: 'pw' });
  assert.equal(r.address, address);
  assert.equal((await onb2.status()).hasKey, true);
});

test('signLogin requires unlocked and produces a verifiable gc-msg-v1 proof', async () => {
  const onb = makeOnboarding({ store: fakeStore(), window: SECURE });
  await onb.create({ passphrase: 'pw' });
  await onb.lock();
  await assert.rejects(() => onb.signLogin('login:abc'), NoSigningKeyError);
  await onb.unlock({ passphrase: 'pw' });
  const proof = await onb.signLogin('login:abc');
  assert.equal(proof.scheme, 'gc-msg-v1');
  assert.equal(proof.message, 'login:abc');
  const v = await verifyMessage(proof, { maxAge: Number.MAX_SAFE_INTEGER });
  assert.equal(v.valid, true);
});

test('forget deletes the device record', async () => {
  const onb = makeOnboarding({ store: fakeStore(), window: SECURE });
  await onb.create({ passphrase: 'pw' });
  await onb.forget();
  const s = await onb.status();
  assert.equal(s.hasKey, false);
  assert.equal(s.unlocked, false);
});
```

- [ ] **Step 2: Run test to verify it fails**

Run: `node --test clients/signing-key/gc-onboarding.test.mjs`
Expected: FAIL — cannot find module `./gc-onboarding.mjs`.

- [ ] **Step 3: Write the controller**

Create `clients/signing-key/gc-onboarding.mjs`:

```js
// Headless, style-agnostic signing-key onboarding controller. Orchestrates the
// low-level gc-* modules into create / back up / restore / unlock / sign-login
// over a single in-memory unlocked-key holder. NO DOM, NO CSS, NO framework:
// the consuming app owns all markup and renders from status() + onChange().
import { SigningKey } from './gc-signing-key.mjs';
import * as keyring from './gc-keyring.mjs';
import { makeIdbStore } from './gc-store-idb.mjs';
import { exportEncrypted, importEncrypted } from './gc-backup.mjs';
import { makeWebauthnPasskey } from './gc-passkey-webauthn.mjs';
import { signMessage } from './gc-message.mjs';
import {
  NoSigningKeyError,
  UnsupportedError,
  BadBackupError,
  BadPassphraseError,
} from './gc-errors.mjs';

// Re-exported so consuming apps can catch by type and render their own copy.
export {
  NoSigningKeyError, UnsupportedError, BadBackupError, BadPassphraseError,
};

function backupFilename(address) {
  const slug = (address || 'signing-key')
    .replace(/[^A-Za-z0-9]/g, '')
    .slice(0, 12);
  return `gc-signing-key-backup-${slug || 'signing-key'}.json`;
}

export function makeOnboarding({
  store = makeIdbStore(),
  rpId,
  rpName,
  passkey = null,
  window: win = globalThis.window,
} = {}) {
  // A passkey adapter is built from rpId/rpName unless one is injected; absent
  // both, passkey features stay unavailable (status reports passkeySupported:false).
  const pk = passkey ?? ((rpId && rpName) ? makeWebauthnPasskey({ rpId, rpName }) : null);

  let key = null; // the in-memory unlocked SigningKey, or null when locked
  const listeners = new Set();

  const secureContext = () => Boolean(win && win.isSecureContext);

  async function status() {
    const rec = await store.get();
    const address = key ? await key.address() : (rec ? rec.address : null);
    let passkeySupported = false;
    if (pk && secureContext()) {
      try {
        passkeySupported = await pk.isSupported();
      } catch {
        passkeySupported = false;
      }
    }
    return {
      hasKey: Boolean(rec),
      unlocked: Boolean(key),
      address,
      passkeySupported,
      secureContext: secureContext(),
    };
  }

  async function notify() {
    const snapshot = await status();
    for (const fn of listeners) fn(snapshot);
  }

  function onChange(fn) {
    listeners.add(fn);
    return () => listeners.delete(fn);
  }

  function passkeyIds(userName, address) {
    return { userId: address, userName: userName || address };
  }

  async function create({ passphrase, withPasskey = false, userName } = {}) {
    const sk = await SigningKey.generate();
    await keyring.enroll(sk, { store }, { passphrase });
    const address = await sk.address();
    if (withPasskey && pk) {
      await keyring.addPasskey(
        { store, passkey: pk }, { passphrase }, passkeyIds(userName, address),
      );
    }
    key = sk;
    await notify();
    return { address };
  }

  async function unlock({ passphrase, passkey: usePasskey } = {}) {
    key = await keyring.unlock(
      { store, passkey: usePasskey ? pk : undefined },
      { passphrase },
    );
    await notify();
    return { address: await key.address() };
  }

  async function restore({ backup, passphrase } = {}) {
    const artifact = typeof backup === 'string' ? JSON.parse(backup) : backup;
    const sk = await importEncrypted(artifact, passphrase);
    await keyring.enroll(sk, { store }, { passphrase });
    key = sk;
    await notify();
    return { address: await sk.address() };
  }

  async function backup({ passphrase } = {}) {
    if (!key) {
      key = await keyring.unlock({ store }, { passphrase });
    }
    const artifact = await exportEncrypted(key, passphrase);
    await notify();
    return { artifact, filename: backupFilename(await key.address()) };
  }

  async function addPasskey({ passphrase, userName } = {}) {
    const rec = await store.get();
    const address = await keyring.addPasskey(
      { store, passkey: pk },
      { passphrase },
      passkeyIds(userName, rec ? rec.address : undefined),
    );
    await notify();
    return { address };
  }

  async function signLogin(challenge, { timestamp } = {}) {
    if (!key) {
      throw new NoSigningKeyError('locked: unlock before signing a login challenge');
    }
    return signMessage(key, challenge, { timestamp });
  }

  async function lock() {
    key = null;
    await notify();
  }

  async function forget() {
    await keyring.clear(store);
    key = null;
    await notify();
  }

  return {
    status, onChange, create, unlock, restore, backup, addPasskey,
    signLogin, lock, forget,
  };
}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `node --test clients/signing-key/gc-onboarding.test.mjs`
Expected: PASS (8 tests).

> If `verifyMessage` reports `valid:false` with reason `expired`, the `maxAge`
> override in the test is wrong — confirm `verifyMessage`'s `maxAge` unit
> (seconds) and pass a large value; the proof itself is valid.

- [ ] **Step 5: Commit**

```bash
git add clients/signing-key/gc-onboarding.mjs clients/signing-key/gc-onboarding.test.mjs
git commit -m "feat(signing-key): gc-onboarding headless controller"
```

---

## Task 3: Vendor to static + full-suite verification

**Files:**
- Generated: `src/gumptionchain/static/signing-key/gc-onboarding.mjs` (via the sync script)

- [ ] **Step 1: Sync the module into the served static tree**

Run: `python3 scripts/sync_signing_key.py`
Expected: output includes `vendored gc-onboarding.mjs`. (The `.test.mjs` is intentionally NOT vendored.)

- [ ] **Step 2: Confirm the vendored copy matches source**

Run: `git diff --no-index clients/signing-key/gc-onboarding.mjs src/gumptionchain/static/signing-key/gc-onboarding.mjs`
Expected: no diff (identical).

- [ ] **Step 3: Run the JS suite as CI does**

Run: `node --test clients/signing-key/*.test.mjs src/gumptionchain/static/js/*.test.mjs`
Expected: all pass (including the new 8).

- [ ] **Step 4: Run the Python suite (incl. the vendored-parity gate + static helper)**

Run: `uv run pytest tests/test_signing_key_vendored.py tests/test_static_assets.py -q`
Expected: PASS.

- [ ] **Step 5: Commit the vendored module**

```bash
git add src/gumptionchain/static/signing-key/gc-onboarding.mjs
git commit -m "build(signing-key): vendor gc-onboarding.mjs into static"
```

---

## Task 4: Docs — flip "Recommended follow-up" to Shipped

**Files:**
- Modify: `docs/key-onboarding-for-egu-apps.md`

- [ ] **Step 1: Replace the "Recommended follow-up" section**

In `docs/key-onboarding-for-egu-apps.md`, replace the `## Recommended follow-up` section body with:

```markdown
## The reusable drop-in (shipped)

Base ships **`gc-onboarding.mjs`** — a headless, style-agnostic controller that
orchestrates the modules above into the full flow. The consuming app owns all
DOM/CSS and drives the controller; logic and improvements land once for everyone.

Served at `/static/gumptionchain/signing-key/gc-onboarding.mjs`. Non-node
consumers that don't register the chain-explorer blueprint can mount the assets
with `static_assets_blueprint()`:

    from gumptionchain import static_assets_blueprint
    app.register_blueprint(static_assets_blueprint())  # serves /static/gumptionchain/...

Minimal use:

    import { makeOnboarding } from '/static/gumptionchain/signing-key/gc-onboarding.mjs';
    const onb = makeOnboarding({ rpId: location.hostname, rpName: 'My App' });
    onb.onChange(render);                       // app re-renders its own DOM
    await onb.status();                         // { hasKey, unlocked, address, passkeySupported, secureContext }
    await onb.create({ passphrase, withPasskey: true });
    await onb.unlock({ passphrase });           // or { passkey: true }
    const { artifact, filename } = await onb.backup({ passphrase });
    await onb.restore({ backup, passphrase });
    const proof = await onb.signLogin(challenge); // gc-msg-v1, POST to your server
    onb.lock();

Auto-lock is the app's policy — wire it to your own lifecycle, e.g.:

    document.addEventListener('visibilitychange', () => {
      if (document.hidden) onb.lock();
    });
```

- [ ] **Step 2: Commit**

```bash
git add docs/key-onboarding-for-egu-apps.md
git commit -m "docs: gc-onboarding drop-in shipped (key-onboarding guide)"
```

---

## After all tasks

- Run the **full** gates once more: `uv run pytest -q`, `node --test clients/signing-key/*.test.mjs src/gumptionchain/static/js/*.test.mjs`, `uv run ruff check src tests app.py`, `uv run ruff format --check src tests app.py`, `uv run mypy`.
- Open the PR. **Report back to the gumptactoe session** in the PR body + summary:
  the module path (`/static/gumptionchain/signing-key/gc-onboarding.mjs`), the
  `makeOnboarding` API (options + the method table above), the
  `static_assets_blueprint()` mount snippet, and the **merge commit SHA** to pin.
- The **hub refactor** (consume the controller in gumption-hub) is a separate
  follow-up PR in that repo.
