# EGU #262 — Transact Signing-Key Flow Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Rebuild the `/transact` key UX as an explicit three-state signing-key
panel (inline create / locked / unlocked) with markup-level action gating
and the one-session key collapsed under an Advanced disclosure.

**Architecture:** Per the approved spec
(`docs/superpowers/specs/2026-06-11-egu-262-transact-signet-flow-design.md`).
A pure decision function (`whichKeyPanel`) picks the visible state;
the shared `_key_import.html` partial becomes the state-machine markup
(so `/advanced` inherits it); `transact-glue.mjs` gains create/lock
wiring and toggles the build buttons' `disabled`. No server-side changes.

**Tech Stack:** Jinja templates, vanilla ESM (`transact-glue.mjs`),
`node --test` for JS (CI already runs
`node --test clients/wallet/*.test.mjs src/gumptionchain/static/js/*.test.mjs`),
pytest for template markup.

**Branch:** `feat/egu-262-transact-signet-flow` off `main` (after the
docs PR merges). Single implementation PR.

**Verified-in-code facts the implementer needs:**

- `transact-glue.mjs` (`src/gumptionchain/static/js/`) already exports
  the DOM-free `whichKeyControls({hasWallet, passkeySupported})`
  (~line 283) used by `renderKeyControls()` (~line 423), and
  `src/gumptionchain/static/js/transact-glue.test.mjs` exists and runs
  in CI — read it first; its `whichKeyControls` tests stay green
  through Tasks 1-2 and are deleted in Task 3 along with the function
  and `renderKeyControls` (the new state model replaces all three).
- The keyring record's `address` is plaintext (no unlock needed):
  `store.get()` → `{version, address, wallet_ct, wraps}` or `null`
  (`clients/wallet/gc-keyring.mjs:18-21`). Create =
  `keyring.enroll(wallet, {store}, {passphrase})` after
  `Wallet.generate()` — exactly `wallet-glue.mjs:219-244`'s handler.
- Trust acknowledgment helpers are EXPORTED from
  `src/gumptionchain/static/js/wallet-glue.mjs:60-76`
  (`TRUST_ACK_KEY`, `readTrustAck(storage)`, `writeTrustAck(storage)`),
  and `wallet-glue.mjs` has no module-body side effects (its own node
  test imports it) — `transact-glue.mjs` imports them directly.
- The in-memory wallet lives in the injected `session`
  (`wallet-session.mjs`): `session.setWallet(w)`, `session.getWallet()`,
  `session.lock()`, `session.onLock(cb)` (the existing `onLock` callback
  in transact-glue ~line 703 re-renders — repoint it), and
  `session.installAutoLock(...)` stays untouched.
- `unlockSaved({store, session, passphrase|passkey})` (~line 295) is the
  saved-unlock path; the ephemeral import handler also ends in
  `session.setWallet`. The new `unlockSource` module variable must be
  set in BOTH paths ('saved' / 'session') and cleared in the `onLock`
  callback.
- `Wallet.generate()` and `wallet.address()` are async
  (`clients/wallet/gc-wallet.mjs`).
- Markup-pinning pytest to reconcile: `tests/test_transact_page.py:36-38`
  (`id="saved-wallet"`, 'Unlock your saved wallet',
  `id="unlock-passphrase"`), `tests/test_advanced_page.py:22-24`
  (`id="saved-wallet"`, `id="key-b58"`, `id="import-key-btn"`).
  `tests/test_wallet_page.py:31` pins `unlock-passphrase` on /wallet —
  UNAFFECTED (different page, untouched).
- Run JS tests: `node --test src/gumptionchain/static/js/*.test.mjs`
  (or a single file: `node --test <path>`); node is available.
- Style: templates use Bootstrap 5 classes; collapse idiom =
  `data-bs-toggle="collapse" href="#id"` (see the broadcast section in
  `advanced.html`). Python gates: ruff 80-col single quotes, mypy
  strict (no Python changes expected beyond tests).

## File structure

```
src/gumptionchain/static/js/transact-glue.mjs       # Tasks 1, 3
src/gumptionchain/static/js/transact-glue.test.mjs  # Tasks 1, 3
src/gumptionchain/templates/_key_import.html        # Task 2 (rewrite)
src/gumptionchain/templates/transact.html           # Task 2
tests/test_transact_page.py                         # Task 2
tests/test_advanced_page.py                         # Task 2
```

---

### Task 1: `whichKeyPanel` decision function (node TDD)

**Files:**
- Modify: `src/gumptionchain/static/js/transact-glue.mjs`
- Modify: `src/gumptionchain/static/js/transact-glue.test.mjs`

- [ ] **Step 1: Read the existing test file**, note any
`whichKeyControls` tests. They stay green in this task (the old
function and markup survive until Tasks 2-3); Task 3 deletes them with
the function.

- [ ] **Step 2: Write the failing tests** (append to
`transact-glue.test.mjs`, matching its import style):

```javascript
test('whichKeyPanel: no record -> none, actions disabled', () => {
  const c = whichKeyPanel({
    hasRecord: false,
    unlockedKind: null,
    passkeySupported: true,
  });
  assert.equal(c.state, 'none');
  assert.equal(c.actionsEnabled, false);
  assert.equal(c.badge, null);
  assert.equal(c.showUnlockPasskey, false);
});

test('whichKeyPanel: record + locked -> locked, passkey button per support', () => {
  const locked = whichKeyPanel({
    hasRecord: true,
    unlockedKind: null,
    passkeySupported: true,
  });
  assert.equal(locked.state, 'locked');
  assert.equal(locked.actionsEnabled, false);
  assert.equal(locked.showUnlockPasskey, true);
  const noPasskey = whichKeyPanel({
    hasRecord: true,
    unlockedKind: null,
    passkeySupported: false,
  });
  assert.equal(noPasskey.showUnlockPasskey, false);
});

test('whichKeyPanel: unlocked saved -> unlocked, actions enabled, saved badge', () => {
  const c = whichKeyPanel({
    hasRecord: true,
    unlockedKind: 'saved',
    passkeySupported: true,
  });
  assert.equal(c.state, 'unlocked');
  assert.equal(c.actionsEnabled, true);
  assert.equal(c.badge, 'saved');
});

test('whichKeyPanel: session key -> unlocked even with no record', () => {
  const c = whichKeyPanel({
    hasRecord: false,
    unlockedKind: 'session',
    passkeySupported: false,
  });
  assert.equal(c.state, 'unlocked');
  assert.equal(c.actionsEnabled, true);
  assert.equal(c.badge, 'session');
});
```

Add `whichKeyPanel` to the file's import list from
`./transact-glue.mjs`.

- [ ] **Step 3: Run to verify failure**

Run: `node --test src/gumptionchain/static/js/transact-glue.test.mjs`
Expected: the new tests FAIL (whichKeyPanel not exported)

- [ ] **Step 4: Implement** in `transact-glue.mjs`, ADDED ALONGSIDE
`whichKeyControls` (which keeps serving the old markup until Task 3
deletes it together with `renderKeyControls` and their tests — clean
task boundary; both functions coexist after this task):

```javascript
// Pure state decision for the key panel (#262). unlockedKind is
// null (locked / no key), 'saved' (unlocked from the keyring), or
// 'session' (one-session key imported under Advanced).
export function whichKeyPanel({
  hasRecord,
  unlockedKind,
  passkeySupported,
}) {
  if (unlockedKind) {
    return {
      state: 'unlocked',
      badge: unlockedKind,
      actionsEnabled: true,
      showUnlockPasskey: false,
    };
  }
  if (hasRecord) {
    return {
      state: 'locked',
      badge: null,
      actionsEnabled: false,
      showUnlockPasskey: !!passkeySupported,
    };
  }
  return {
    state: 'none',
    badge: null,
    actionsEnabled: false,
    showUnlockPasskey: false,
  };
}
```

- [ ] **Step 5: Run to verify pass**

Run: `node --test src/gumptionchain/static/js/transact-glue.test.mjs`
Expected: ALL pass (pre-existing whichKeyControls tests included —
nothing replaced yet)

- [ ] **Step 6: Commit**

```bash
git add src/gumptionchain/static/js/transact-glue.mjs src/gumptionchain/static/js/transact-glue.test.mjs
git commit -m "feat(transact): whichKeyPanel state decision (#262)"
```

---

### Task 2: state-machine markup — partial + transact page (pytest TDD)

**Files:**
- Rewrite: `src/gumptionchain/templates/_key_import.html`
- Modify: `src/gumptionchain/templates/transact.html`
- Modify: `tests/test_transact_page.py`, `tests/test_advanced_page.py`

- [ ] **Step 1: Write the failing tests.** In
`tests/test_transact_page.py`, REPLACE
`test_transact_page_has_saved_wallet_unlock_markup` with:

```python
def test_transact_page_key_panel_states(app, test_client):
    with app.app_context():
        body = str(test_client.get('/transact').data)
        # The three-state key panel (#262): exactly one state is
        # shown by JS; all ship in markup.
        assert 'data-key-state="none"' in body
        assert 'data-key-state="locked"' in body
        assert 'data-key-state="unlocked"' in body
        # Inline mini-create (the conversion moment).
        assert 'id="key-create-passphrase"' in body
        assert 'id="key-trust-ack"' in body
        assert 'id="key-create-btn"' in body
        assert 'Create your signing key' in body  # noqa: dup-ok
        # Explicit unlock state.
        assert 'id="unlock-passphrase"' in body
        assert 'id="unlock-saved-btn"' in body
        # Unlocked state: badge + explicit lock.
        assert 'id="key-badge"' in body
        assert 'id="key-lock-btn"' in body
        # One-session key collapsed under Advanced.
        assert 'id="session-key"' in body
        assert 'class="collapse' in body
        assert 'one-session key' in body
        assert 'id="key-b58"' in body
        assert 'id="import-key-btn"' in body
        # Key-first copy; self-explanatory, no glossary needed.
        assert 'Create your signing key' in body


def test_transact_actions_disabled_until_unlocked(app, test_client):
    with app.app_context():
        body = str(test_client.get('/transact').data)
        # Markup-level gating: build/confirm ship disabled; the glue
        # enables them only in the unlocked state.
        assert 'id="build-review-btn"' in body
        assert 'disabled' in body.split('id="build-review-btn"')[1][:120]
        assert 'disabled' in body.split('id="confirm-submit-btn"')[1][:120]
```

In `tests/test_advanced_page.py`, replace the three key-area
assertions (lines ~22-24) with:

```python
        # The shared key panel (#262) renders here too.
        assert 'data-key-state="none"' in body
        assert 'data-key-state="locked"' in body
        assert 'id="key-b58"' in body
        assert 'id="import-key-btn"' in body
```

(Keep the rest of that test untouched.)

- [ ] **Step 2: Run to verify failure**

Run: `uv run pytest tests/test_transact_page.py tests/test_advanced_page.py -q`
Expected: FAIL (new ids absent)

- [ ] **Step 3: Rewrite `_key_import.html`** in full:

```html
{# Key panel (#262): explicit three-state machine — no key on
   this device (inline create) / saved + locked (explicit unlock) /
   unlocked (badge + Lock). Shared by transact.html and advanced.html;
   transact-glue.mjs shows exactly one state container and gates the
   page actions. The one-session key for power users is collapsed
   under the Advanced disclosure in every state. #}
<div id="key-panel">
  <div data-key-state="none" hidden>
    <div class="fw-semibold">Create your signing key</div>
    <p class="small text-muted mb-2">
      Your signing key marks your stakes as yours. It is created in
      your browser and saved encrypted on this device &mdash; it is
      never sent anywhere.
    </p>
    <label for="key-create-passphrase" class="form-label">Passphrase</label>
    <input id="key-create-passphrase" type="password" autocomplete="off"
           class="form-control" placeholder="choose a passphrase">
    <div class="form-check mt-2">
      <input id="key-trust-ack" type="checkbox" class="form-check-input">
      <label for="key-trust-ack" class="form-check-label small">
        Persist only on a node you trust: this saves your encrypted
        key in this browser, on this site.
      </label>
    </div>
    <button id="key-create-btn" class="btn btn-primary btn-sm mt-2">
      Create your signing key
    </button>
    <div id="key-create-status" class="mt-2 small"></div>
  </div>

  <div data-key-state="locked" hidden>
    <div class="fw-semibold">
      Your signing key
      <span class="badge text-bg-secondary">locked</span>
    </div>
    <p class="small text-muted mb-2">
      Saved on this device as <code data-key-address></code>.
      Unlock it to sign.
    </p>
    <label for="unlock-passphrase" class="form-label">Passphrase</label>
    <input id="unlock-passphrase" type="password" autocomplete="off"
           class="form-control" placeholder="your key passphrase">
    <div class="mt-2">
      <button id="unlock-saved-btn" class="btn btn-primary btn-sm">
        Unlock
      </button>
      <button id="unlock-saved-passkey-btn"
              class="btn btn-outline-primary btn-sm" hidden>
        Unlock with passkey
      </button>
    </div>
    <div id="unlock-status" class="mt-2 small"></div>
  </div>

  <div data-key-state="unlocked" hidden>
    <span id="key-badge" class="badge text-bg-success"></span>
    <button id="key-lock-btn" class="btn btn-outline-secondary btn-sm">
      Lock
    </button>
    <div id="key-backup-nudge" class="small text-muted mt-2" hidden>
      Key created. <a href="{{ url_for('browser.wallet_view') }}">Back
      it up on the Wallet page</a> &mdash; the backup is your only
      recovery.
    </div>
  </div>

  <div class="mt-3">
    <a class="small text-decoration-none" data-bs-toggle="collapse"
       href="#session-key" role="button" aria-expanded="false"
       aria-controls="session-key">
      &#9656; Advanced: use a one-session key instead
    </a>
    <div id="session-key" class="collapse mt-2">
      <label for="key-b58" class="form-label">
        One-session key (base58 private key) &mdash; held in memory
        only, nothing is saved
      </label>
      <textarea id="key-b58" class="form-control" rows="2"
                placeholder="paste base58 private key"></textarea>
      <div class="mt-2">
        <label for="key-pem" class="form-label">or upload a .pem</label>
        <input id="key-pem" type="file" accept=".pem" class="form-control">
      </div>
      <div class="mt-2">
        <button id="import-key-btn" class="btn btn-secondary btn-sm">
          Import key
        </button>
        <button id="forget-key-btn" class="btn btn-outline-danger btn-sm">
          Forget key
        </button>
      </div>
      <div id="key-status" class="mt-2 small"></div>
    </div>
  </div>
</div>
```

(The old partial's `saved-wallet` container id and "or use a key just
for this session" copy are gone by design.)

- [ ] **Step 4: Restructure `transact.html`.** Three surgical changes
(read the file; the form-fields block and the scripts block are
untouched):

(a) Replace the alert paragraph with one line:

```html
  <div class="row my-3"><div class="col">
    <div class="alert alert-warning" role="alert">
      <strong>Your private key never leaves your browser</strong> &mdash;
      signing happens here, and only signatures are sent.
    </div>
  </div></div>
```

(b) The Build & sign card ends after the last `data-field-group` block
(close the card right after the rescind-kind select's `</div>`); the
`{% include "_key_import.html" %}` and the `<hr>` above it move OUT of
the card. After the form card, add:

```html
  <!-- The key gate (#262): filling the form is free; signing
       requires the explicit unlock above the action. -->
  <div class="row my-3"><div class="col">
    <div class="card"><div class="card-body">
      {% include "_key_import.html" %}
    </div></div>
  </div></div>

  <div class="row my-3"><div class="col">
    <div class="card"><div class="card-body">
      <button id="build-review-btn" class="btn btn-primary" disabled>
        Build &amp; review
      </button>
      <button id="confirm-submit-btn" class="btn btn-success" hidden disabled>
        Confirm &amp; submit
      </button>
      <div class="mt-3">
        <pre id="confirm-area" class="small bg-light p-2"></pre>
        <div id="build-result" class="mt-2"></div>
      </div>
    </div></div>
  </div></div>
```

(the old in-card button/confirm-area block is removed — same ids, new
location, plus `disabled` on both buttons).

(c) Page copy: the card title stays `Build &amp; sign a transaction`.

- [ ] **Step 5: Run to verify pass**

Run: `uv run pytest tests/test_transact_page.py tests/test_advanced_page.py tests/test_ui_seam.py -q`
Expected: ALL pass (seam tests assert copy that still exists — verify;
if `test_consumer_base_html_reskins_transact_page` pinned removed copy,
update its content assertion to `Build &amp; sign`-family text and
report the change).

- [ ] **Step 6: Commit**

```bash
git add src/gumptionchain/templates tests/test_transact_page.py tests/test_advanced_page.py tests/test_ui_seam.py
git commit -m "feat(transact): three-state key panel markup, gated actions (#262)"
```

---

### Task 3: glue wiring — create, lock, state rendering, gating

**Files:**
- Modify: `src/gumptionchain/static/js/transact-glue.mjs`
- Modify: `src/gumptionchain/static/js/transact-glue.test.mjs` (only if
  pure helpers are added)

- [ ] **Step 1: Imports + state.** Add to transact-glue.mjs imports:

```javascript
import { readTrustAck, writeTrustAck } from './wallet-glue.mjs';
```

Inside `init()`, add a module of new element lookups next to the
existing ones, and the unlock-source tracker:

```javascript
  const createPassphrase = $('#key-create-passphrase');
  const createTrustAck = $('#key-trust-ack');
  const createBtn = $('#key-create-btn');
  const createStatus = $('#key-create-status');
  const keyBadge = $('#key-badge');
  const lockBtn = $('#key-lock-btn');
  const backupNudge = $('#key-backup-nudge');
  const storage = win ? win.localStorage : undefined;
  // 'saved' | 'session' | null — which key source unlocked the page.
  let unlockSource = null;
```

- [ ] **Step 2: Replace `renderKeyControls` with `renderKeyPanel`**
(NOW delete `whichKeyControls`, `renderKeyControls`, and the old
whichKeyControls tests — the new state containers from Task 2 are the
only markup left; note the interim state between Tasks 2 and 3 renders
an all-hidden panel, which is fine inside a single PR):

```javascript
  async function renderKeyPanel() {
    let rec = null;
    try {
      rec = await store.get();
    } catch {
      // IDB unavailable: fall through to the no-key state; the
      // Advanced one-session key still works.
      if (createStatus) {
        setStatus(
          createStatus,
          'Saved keys are unavailable in this browser; use the ' +
            'Advanced one-session key below.',
          'error',
        );
      }
    }
    const c = whichKeyPanel({
      hasRecord: rec !== null,
      unlockedKind: session.getWallet() ? unlockSource : null,
      passkeySupported: passkey != null,
    });
    for (const el of root.querySelectorAll('[data-key-state]')) {
      show(el, el.dataset.keyState === c.state);
    }
    show(unlockPasskeyBtn, c.showUnlockPasskey);
    const addrEl = root.querySelector('[data-key-address]');
    if (addrEl && rec) addrEl.textContent = `${rec.address.slice(0, 12)}…`;
    if (keyBadge && c.state === 'unlocked') {
      const addr = await session.getWallet().address();
      keyBadge.textContent =
        c.badge === 'session'
          ? `one-session key · ${addr.slice(0, 12)}…`
          : `signing as ${addr.slice(0, 12)}…`;
    }
    if (backupNudge && c.state !== 'unlocked') show(backupNudge, false);
    for (const btn of [buildBtn, confirmBtn]) {
      if (btn) btn.disabled = !c.actionsEnabled;
    }
  }
```

Update every existing `renderKeyControls()` call site (unlock handlers,
the `onLock` callback, the bootstrap IIFE) to `renderKeyPanel()`, and
in the `onLock` callback also clear the source: `unlockSource = null;`.
In the two saved-unlock handlers set `unlockSource = 'saved'` after a
successful `unlockSaved(...)`; in the ephemeral import handler set
`unlockSource = 'session'` after its `session.setWallet(...)`; in the
forget handler clear it (`unlockSource = null`) before/with
`session.lock()`.

NOTE: `confirmBtn.disabled` interacts with the existing build flow —
the build handler reveals `confirmBtn` (`confirmBtn.hidden = false`);
verify it doesn't also need `confirmBtn.disabled = false` (it will be
enabled already since building requires the unlocked state — but
`resetPending()` hides it again; leave `disabled` driven ONLY by
`renderKeyPanel` and `hidden` by the build flow, which composes
correctly).

- [ ] **Step 3: The create handler** (next to the unlock handlers):

```javascript
  if (createBtn) {
    createBtn.addEventListener('click', async () => {
      const passphrase = createPassphrase ? createPassphrase.value : '';
      if (!passphrase) {
        setStatus(createStatus, 'Set a passphrase first.', 'error');
        return;
      }
      if (!readTrustAck(storage)) {
        if (createTrustAck && createTrustAck.checked) {
          writeTrustAck(storage);
        } else {
          setStatus(
            createStatus,
            'Acknowledge the trust note first: persist only on a node ' +
              'you trust.',
            'error',
          );
          return;
        }
      }
      try {
        const wallet = await Wallet.generate();
        await keyring.enroll(wallet, { store }, { passphrase });
        session.setWallet(wallet);
        unlockSource = 'saved';
        createPassphrase.value = '';
        if (backupNudge) show(backupNudge, true);
        await renderKeyPanel();
      } catch (e) {
        setStatus(createStatus, `Could not create: ${msgOf(e)}`, 'error');
      }
    });
  }

  if (lockBtn) {
    lockBtn.addEventListener('click', () => {
      session.lock();
    });
  }
```

(`session.lock()` fires the `onLock` callback, which clears
`unlockSource` and re-renders — no direct render call needed here.)

- [ ] **Step 4: Verify the JS suite + lint posture**

Run: `node --test src/gumptionchain/static/js/*.test.mjs clients/wallet/*.test.mjs`
Expected: ALL pass. If any existing transact-glue test stubbed the old
`#saved-wallet` rendering, update it to the state-container model (and
report the change).

- [ ] **Step 5: Manual checklist on a dev node** (operator step —
document the results in the PR body): fresh browser profile →
`/transact` shows Create state, buttons disabled → create → unlocked
badge + nudge, buttons enabled → build+submit a stake → Lock → locked
state, buttons disabled → unlock → enabled; Advanced disclosure: import
b58 → `one-session key` badge; Forget → back to locked/none;
`/advanced` shows the same panel and its tools require the unlocked
state's key.

- [ ] **Step 6: Commit**

```bash
git add src/gumptionchain/static/js
git commit -m "feat(transact): key panel controller — create, lock, gated actions (#262)"
```

---

### Task 4: Gates + PR + sibling-sweep issue

- [ ] `uv run pytest -q` (full suite)
- [ ] `node --test clients/wallet/*.test.mjs src/gumptionchain/static/js/*.test.mjs`
- [ ] `uv run ruff format --check src tests && uv run ruff check src tests && uv run mypy`
- [ ] `uv run pre-commit run --all-files` (app.py findings are
  pre-existing, tracked in #256 — ignore those two)
- [ ] File the sibling issue: "Base browser key copy sweep —
  /wallet, /advanced, /verify" referencing #262's vocabulary section
  and hub#30's scope-split comment (operator surfaces keep 'wallet').
- [ ] PR `feat(browser): explicit signing-key states on /transact — inline
  create, gated signing (#262)`; body includes the manual-checklist
  results; subagent review; hold for author review.
