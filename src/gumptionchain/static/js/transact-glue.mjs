// Base /transact glue: build (via the node's authed server-side endpoints),
// sign client-side with an unlocked key, and submit. Plus a "broadcast a
// pre-signed txn" mode. The active Wallet lives ONLY in the shared per-page
// wallet-session holder — never persisted from here, never sent (only the
// signature + public key leave the browser). Two ways to obtain that wallet:
//   - "Unlock your saved wallet" — decrypt the gc-keyring record persisted on
//     /wallet (passphrase or, on a secure origin, passkey), OR
//   - "use a key just for this session" — paste a base58 private key (ephemeral
//     import; nothing is saved).
// Either way the unlocked key is subject to the same auto-lock policy (idle /
// tab-hide / page-unload / manual forget) via wallet-session.
//
// The pure helpers (buildQuery / submitPath / responseMessage / buildUnsigned /
// signAndSubmit / whichKeyControls / unlockSaved) are exported and DOM-free so
// they can be unit-tested with fakes. The DOM wiring is in init().
import { Wallet } from '../wallet/gc-wallet.mjs';
import { signHeaders } from '../wallet/gc-sig.mjs';
import { base64encode } from '../wallet/gc-crypto.mjs';
import { signStakeAttestation } from '../wallet/gc-attestation.mjs';
import {
  signUnsignedTxn,
  txid as computeTxid,
} from '../wallet/gc-transaction.mjs';
import * as keyring from '../wallet/gc-keyring.mjs';
import { makeIdbStore } from '../wallet/gc-store-idb.mjs';
import { session as defaultSession } from './wallet-session.mjs';
import { makePasskey } from './wallet-passkey.mjs';

const API_PREFIX = '/api';

// The fields each transaction type sends. public_key is always added from the
// imported wallet; the rest come from the form. (subject is the RAW UTF-8
// subject — the server encodes it itself, so it must NOT be pre-encoded.)
const TYPE_FIELDS = {
  transfer: ['amount', 'address'],
  opposition: ['amount', 'subject'],
  support: ['amount', 'subject'],
  rescind: ['amount', 'subject', 'kind'],
};

// Build the EXACT query string sent for a build GET. The same string is used
// for the fetch URL and the gc-sig canonical, so consistency is what matters.
export function buildQuery(type, fields) {
  const names = TYPE_FIELDS[type];
  if (!names) {
    throw new Error(`unknown transaction type: ${type}`);
  }
  const params = new URLSearchParams();
  // public_key first by convention; order is irrelevant to the server (it
  // reconstructs the canonical from the actual request), but stable for tests.
  if (fields.publicKey != null) {
    params.set('public_key', fields.publicKey);
  }
  for (const name of names) {
    const value = fields[name];
    if (value != null && value !== '') {
      params.set(name, String(value));
    }
  }
  return params.toString();
}

// /api/transaction/<txid>, with the txid path segment encoded so a malformed
// txid can't reshape the request into an unintended path/query.
export function submitPath(txid) {
  return `${API_PREFIX}/transaction/${encodeURIComponent(txid)}`;
}

// Map a submit/build response to a single user-facing string. Each documented
// status is surfaced distinctly (closed node, mempool full, validation).
export function responseMessage(status, body) {
  const detail = body && typeof body.error === 'string' ? body.error : '';
  if (status === 200 || status === 201 || status === 202) {
    return 'Transaction submitted and received by the node.';
  }
  if (status === 403) {
    return (
      'This node restricts transacting: your address is not authorized ' +
      '(not in TRANSACTOR_ADDRESSES on this node).'
    );
  }
  if (status === 503) {
    return 'The node is busy: its mempool is full. Try again shortly.';
  }
  if (status === 400) {
    return `The node rejected the transaction: ${detail || 'validation error'}.`;
  }
  return `Unexpected response from the node (HTTP ${status})${
    detail ? `: ${detail}` : ''
  }.`;
}

// Read a fetch Response's JSON, tolerating an empty/non-JSON body.
async function readBody(resp) {
  try {
    const text = await resp.text();
    return text ? JSON.parse(text) : {};
  } catch {
    return {};
  }
}

// nowSeconds: gc-sig timestamps are epoch SECONDS (server allows +/-300s).
const nowSeconds = () => Math.floor(Date.now() / 1000);

// Send a gc-sig-v1 authed request. path/query are signed separately so the
// canonical matches what the server reconstructs from the actual request.
async function authedFetch(
  fetchImpl,
  { method, path, query, body, wallet, nodeHost, timestamp },
) {
  const bodyBytes =
    body != null ? new TextEncoder().encode(body) : new Uint8Array();
  const headers = await signHeaders(wallet, {
    method,
    path,
    query,
    body: bodyBytes,
    nodeHost,
    timestamp,
  });
  const url = query ? `${path}?${query}` : path;
  const opts = { method, headers };
  if (body != null) {
    opts.body = body;
    opts.headers = { ...headers, 'Content-Type': 'application/json' };
  }
  return fetchImpl(url, opts);
}

// Build (GET) an unsigned txn and independently verify its txid — WITHOUT
// signing — so the caller can show the parsed txn for explicit human
// confirmation before any signature. Returns { unsigned }. Throws (with a
// user-facing message) if the build GET fails: no signature/POST happens.
export async function buildUnsigned({
  type,
  fields,
  wallet,
  nodeHost,
  fetchImpl = globalThis.fetch,
  timestamp = nowSeconds(),
}) {
  const publicKey = await wallet.publicKeyB64();
  const query = buildQuery(type, { ...fields, publicKey });
  const buildPath = `${API_PREFIX}/transaction/${type}`;
  const buildResp = await authedFetch(fetchImpl, {
    method: 'GET',
    path: buildPath,
    query,
    wallet,
    nodeHost,
    timestamp,
  });
  if (!buildResp.ok) {
    const body = await readBody(buildResp);
    throw new Error(responseMessage(buildResp.status, body));
  }
  const unsigned = await buildResp.json();
  // Honesty check: the node-built txid must match a fresh recompute from its
  // own fields. This is INTEGRITY only — the human confirmation step (showing
  // the parsed txn before this is signed) is what checks intent.
  const recomputed = await computeTxid({ ...unsigned, txid: undefined });
  if (recomputed !== unsigned.txid) {
    throw new Error(
      'txid mismatch: node-built txn does not match its fields',
    );
  }
  return { unsigned };
}

// Sign a previously-built, txid-verified unsigned txn and submit it. Call ONLY
// after the user has confirmed the parsed txn returned by buildUnsigned.
export async function signAndSubmit({
  unsigned,
  wallet,
  nodeHost,
  fetchImpl = globalThis.fetch,
}) {
  const signed = await signUnsignedTxn(unsigned, wallet);
  return submitSigned({ signed, unsigned, wallet, nodeHost, fetchImpl });
}

// Submit an already-signed txn (shared by build-sign and broadcast modes). The
// POST itself is gc-sig authed with the imported key (the submit endpoint is
// authorize_transactor), so broadcast also needs a key for the request
// envelope even though the txn is already signed.
export async function submitSigned({
  signed,
  unsigned = null,
  wallet,
  nodeHost,
  fetchImpl = globalThis.fetch,
  timestamp = nowSeconds(),
}) {
  if (!signed || !signed.txid || !signed.signature) {
    throw new Error(
      'This does not look like a signed transaction ' +
        '(missing txid or signature).',
    );
  }
  const body = JSON.stringify(signed);
  const resp = await authedFetch(fetchImpl, {
    method: 'POST',
    path: submitPath(signed.txid),
    query: '',
    body,
    wallet,
    nodeHost,
    timestamp,
  });
  const respBody = await readBody(resp);
  return {
    unsigned: unsigned ?? signed,
    signed,
    status: resp.status,
    message: responseMessage(resp.status, respBody),
  };
}

// --- Attestation (the producer side of /verify) ----------------------------

// Encode a RAW subject to the base64url, padding-stripped form. This MUST match
// Python's payload.encode_subject (urlsafe_b64encode(raw.encode()).rstrip('=')),
// because /verify compares an attestation's claim against on-chain provenance
// whose subject is the ENCODED form. The literals are locked to a pytest
// (tests/test_encode_subject_parity.py) so the two stay in sync.
export function encodeSubject(raw) {
  return base64encode(new TextEncoder().encode(raw))
    .replace(/\+/g, '-')
    .replace(/\//g, '_')
    .replace(/=+$/, '');
}

// Sign a stake attestation. Takes a RAW subject (consistent with the txn
// builder, which sends the raw subject and lets the server encode it), encodes
// it, and builds the claim with the ENCODED subject before signing — so the
// proof's claim matches on-chain provenance at /verify. Returns the gc-msg-v1
// proof object.
export async function signAttestation({
  txid,
  kind,
  rawSubject,
  amount,
  wallet,
  timestamp,
}) {
  const claim = {
    txid,
    kind,
    subject: encodeSubject(rawSubject),
    amount,
  };
  return signStakeAttestation(wallet, claim, { timestamp });
}

// --- Saved-wallet unlock ----------------------------------------------------

// Which saved-wallet unlock affordances to show, given the observable state.
// The passphrase unlock shows whenever a wallet is persisted on this origin;
// the passkey button shows only when a passkey is actually usable here.
export function whichKeyControls({ hasWallet, passkeySupported }) {
  return {
    showUnlockSaved: !!hasWallet,
    showUnlockPasskey: !!hasWallet && !!passkeySupported,
  };
}

// Unlock the saved (gc-keyring) wallet and hold it in the shared session for
// this page's life (auto-locked like the ephemeral path). passphrase OR passkey
// is supplied. A wrong secret rejects out of the keyring (GCM auth-tag failure)
// and the session is left untouched (still locked). keyringImpl is injectable
// for tests; it defaults to the real gc-keyring.
export async function unlockSaved({
  store,
  session,
  passphrase,
  passkey,
  keyringImpl = keyring,
}) {
  const deps = { store };
  const secrets = {};
  if (passkey) deps.passkey = passkey;
  if (passphrase != null) secrets.passphrase = passphrase;
  const wallet = await keyringImpl.unlock(deps, secrets);
  session.setWallet(wallet);
  return wallet;
}

// --- DOM wiring ------------------------------------------------------------

function setStatus(el, text, kind = 'info') {
  if (!el) return;
  el.textContent = text;
  el.dataset.kind = kind;
}

// Import from a pasted b58 private key (primary path). PEM is a follow-up.
async function importB58(b58) {
  return Wallet.fromPrivateKeyB58(b58.trim());
}

// Render the parsed unsigned txn for explicit confirmation before submit.
function describeUnsigned(unsigned) {
  const lines = [`txid: ${unsigned.txid}`];
  const out = (unsigned.outflows ?? [])
    .map((o) => {
      if (o.address) return `  -> ${o.amount} grains to ${o.address}`;
      if (o.opposition) return `  -> ${o.amount} grains OPPOSE ${o.opposition}`;
      if (o.support) return `  -> ${o.amount} grains SUPPORT ${o.support}`;
      if (o.rescind) {
        return `  -> ${o.amount} grains RESCIND ${o.rescind} (${o.rescind_kind ?? ''})`;
      }
      return `  -> ${o.amount} grains (change)`;
    })
    .join('\n');
  lines.push(`inputs: ${(unsigned.inflows ?? []).length}`);
  lines.push('outputs:');
  lines.push(out);
  return lines.join('\n');
}

// init attaches the handlers. root defaults to document; nodeHost is the
// node's configured host (gc-sig is node-bound). rpName labels the WebAuthn
// passkey RP. store / session / win / doc are injectable but default to the
// real IndexedDB store / shared session / window / document.
export function init(
  root = document,
  {
    nodeHost,
    rpName = 'GumptionChain',
    store = makeIdbStore({}),
    session = defaultSession,
    win = typeof window !== 'undefined' ? window : undefined,
    doc = typeof document !== 'undefined' ? document : undefined,
  } = {},
) {
  const $ = (sel) => root.querySelector(sel);

  // Reveal type-specific fields when the type changes.
  const typeSelect = $('#txn-type');
  const updateFields = () => {
    const names = TYPE_FIELDS[typeSelect.value] ?? [];
    for (const group of root.querySelectorAll('[data-field-group]')) {
      const field = group.dataset.fieldGroup;
      group.hidden = !names.includes(field);
    }
  };
  if (typeSelect) {
    typeSelect.addEventListener('change', updateFields);
    updateFields();
  }

  // --- Saved-wallet unlock (gc-keyring record persisted on /wallet) ---
  const savedSection = $('#saved-wallet');
  const unlockPassphrase = $('#unlock-passphrase');
  const unlockBtn = $('#unlock-saved-btn');
  const unlockPasskeyBtn = $('#unlock-saved-passkey-btn');
  const unlockStatus = $('#unlock-status');

  // Key import (b58 textarea / .pem file) + forget.
  const keyStatus = $('#key-status');
  const b58Input = $('#key-b58');
  const pemInput = $('#key-pem');
  const importBtn = $('#import-key-btn');
  const forgetBtn = $('#forget-key-btn');

  // Cached passkey capability (resolved once below). Drives which unlock
  // controls show, plus the passkey-unlock click.
  let passkey = null;
  // Whether a wallet was unlocked since the last lock — so an idle/hide lock
  // only reports "locked" when there was actually a key to drop.
  let wasUnlocked = false;

  function show(el, visible) {
    if (el) el.hidden = !visible;
  }

  // Clear any passphrase inputs so a secret never lingers in the DOM.
  function clearSecrets() {
    for (const el of root.querySelectorAll('input[type="password"]')) {
      el.value = '';
    }
  }

  // Is a wallet available for signing (from a saved-wallet unlock OR an
  // ephemeral import)? If not, surface the no-key message and return null.
  const NO_KEY_MSG =
    'Unlock your saved wallet or import a key for this session first.';
  function requireWallet(statusEl) {
    const wallet = session.getWallet();
    if (!wallet) {
      setStatus(statusEl, NO_KEY_MSG, 'error');
      return null;
    }
    return wallet;
  }

  // Re-render the saved-wallet unlock controls from the current state. Hidden
  // entirely when no wallet is persisted on this origin (ephemeral import is
  // then the only path).
  async function renderKeyControls() {
    const hasWallet = await keyring.hasWallet(store);
    const c = whichKeyControls({ hasWallet, passkeySupported: passkey != null });
    show(savedSection, c.showUnlockSaved);
    show(unlockPasskeyBtn, c.showUnlockPasskey);
  }

  // After any unlock/import, report the now-available address (in memory only).
  const onUnlocked = async (statusEl, label) => {
    const wallet = session.getWallet();
    wasUnlocked = true;
    setStatus(
      statusEl,
      `${label}: ${await wallet.address()} (in memory only).`,
      'ok',
    );
  };

  if (unlockBtn) {
    unlockBtn.addEventListener('click', async () => {
      try {
        const passphrase = unlockPassphrase ? unlockPassphrase.value : '';
        if (!passphrase) {
          setStatus(unlockStatus, 'Enter your passphrase.', 'error');
          return;
        }
        await unlockSaved({ store, session, passphrase });
        clearSecrets();
        await onUnlocked(
          unlockStatus,
          'Unlocked your saved wallet for this session',
        );
      } catch {
        // Fixed message, no secret echo: a wrong passphrase fails closed in the
        // keyring (GCM auth tag), and the session is left locked.
        setStatus(
          unlockStatus,
          'Could not unlock (wrong passphrase?).',
          'error',
        );
      }
    });
  }

  if (unlockPasskeyBtn) {
    unlockPasskeyBtn.addEventListener('click', async () => {
      try {
        if (!passkey) {
          setStatus(unlockStatus, 'Passkeys are not available here.', 'error');
          return;
        }
        await unlockSaved({ store, session, passkey });
        await onUnlocked(
          unlockStatus,
          'Unlocked your saved wallet with a passkey',
        );
      } catch (e) {
        setStatus(
          unlockStatus,
          `Could not unlock with a passkey: ${msgOf(e)}`,
          'error',
        );
      }
    });
  }

  // --- Ephemeral import (a key just for this session; nothing is saved) ---
  if (importBtn) {
    importBtn.addEventListener('click', async () => {
      try {
        const b58 = b58Input ? b58Input.value : '';
        if (!b58.trim()) {
          setStatus(keyStatus, 'Paste a base58 private key first.', 'error');
          return;
        }
        session.setWallet(await importB58(b58));
        await onUnlocked(keyStatus, 'Key imported');
      } catch (e) {
        session.lock();
        setStatus(keyStatus, `Could not import key: ${msgOf(e)}`, 'error');
      }
    });
  }
  if (pemInput) {
    pemInput.addEventListener('change', async () => {
      // PEM (.pem upload) import is a documented follow-up; b58 is the
      // primary path this PR ships. Surface clearly rather than half-working.
      setStatus(
        keyStatus,
        'PEM upload is not supported yet — paste the base58 private key ' +
          'instead (a follow-up will add .pem import).',
        'error',
      );
      pemInput.value = '';
    });
  }
  if (forgetBtn) {
    forgetBtn.addEventListener('click', () => {
      // Lock clears the session wallet (whether it came from a saved-wallet
      // unlock or an ephemeral import); the persisted ciphertext is untouched.
      session.lock();
      if (b58Input) b58Input.value = '';
      clearSecrets();
      setStatus(keyStatus, 'Key forgotten — cleared from memory.', 'info');
    });
  }

  // Build & review -> (human confirms) -> sign & submit. Two steps so the
  // user sees the parsed txn BEFORE their key signs it.
  const buildResult = $('#build-result');
  const confirmArea = $('#confirm-area');
  const buildBtn = $('#build-review-btn');
  const confirmBtn = $('#confirm-submit-btn');

  // The verified-but-unsigned txn held between the build and confirm clicks.
  let pendingUnsigned = null;
  const resetPending = () => {
    pendingUnsigned = null;
    if (confirmBtn) confirmBtn.hidden = true;
    if (confirmArea) confirmArea.textContent = '';
  };
  // Any edit to type/fields invalidates a pending build (so you can't confirm
  // a txn built from different inputs than what's now on screen).
  for (const el of root.querySelectorAll(
    '#txn-type, [data-field-group] input, [data-field-group] select',
  )) {
    el.addEventListener('input', resetPending);
    el.addEventListener('change', resetPending);
  }

  if (buildBtn) {
    buildBtn.addEventListener('click', async () => {
      resetPending();
      const wallet = requireWallet(buildResult);
      if (!wallet) return;
      const type = typeSelect.value;
      const fields = collectFields(root, type);
      try {
        setStatus(buildResult, 'Building…', 'info');
        const { unsigned } = await buildUnsigned({
          type,
          fields,
          wallet,
          nodeHost,
        });
        pendingUnsigned = unsigned;
        if (confirmArea) {
          confirmArea.textContent = describeUnsigned(unsigned);
        }
        if (confirmBtn) confirmBtn.hidden = false;
        setStatus(
          buildResult,
          'Review the transaction below, then Confirm & submit. ' +
            'Nothing is signed until you confirm.',
          'info',
        );
      } catch (e) {
        setStatus(buildResult, msgOf(e), 'error');
      }
    });
  }

  if (confirmBtn) {
    confirmBtn.addEventListener('click', async () => {
      if (!pendingUnsigned) {
        setStatus(buildResult, 'Build a transaction first.', 'error');
        return;
      }
      const wallet = requireWallet(buildResult);
      if (!wallet) return;
      try {
        setStatus(buildResult, 'Signing & submitting…', 'info');
        const result = await signAndSubmit({
          unsigned: pendingUnsigned,
          wallet,
          nodeHost,
        });
        setStatus(
          buildResult,
          result.message,
          result.status < 400 ? 'ok' : 'error',
        );
        resetPending();
      } catch (e) {
        setStatus(buildResult, msgOf(e), 'error');
      }
    });
  }

  // Broadcast a pre-signed txn (reuses the imported key for the request
  // envelope).
  const broadcastInput = $('#broadcast-input');
  const broadcastResult = $('#broadcast-result');
  const broadcastBtn = $('#broadcast-btn');
  if (broadcastBtn) {
    broadcastBtn.addEventListener('click', async () => {
      const wallet = requireWallet(broadcastResult);
      if (!wallet) return;
      try {
        const signed = JSON.parse(broadcastInput.value);
        setStatus(broadcastResult, 'Submitting…', 'info');
        const result = await submitSigned({
          signed,
          wallet,
          nodeHost,
        });
        setStatus(
          broadcastResult,
          result.message,
          result.status < 400 ? 'ok' : 'error',
        );
      } catch (e) {
        const m = e instanceof SyntaxError ? `Invalid JSON: ${e.message}` : msgOf(e);
        setStatus(broadcastResult, m, 'error');
      }
    });
  }

  // Sign a stake attestation (producer side of /verify). Reuses the imported
  // key. The subject input is RAW (like the txn builder); signAttestation
  // encodes it so the claim matches on-chain provenance at /verify.
  const attTxid = $('#att-txid');
  const attKind = $('#att-kind');
  const attSubject = $('#att-subject');
  const attAmount = $('#att-amount');
  const attBtn = $('#att-sign-btn');
  const attResult = $('#att-result');
  const attProof = $('#att-proof');
  const attCopyBtn = $('#att-copy-btn');

  if (attBtn) {
    attBtn.addEventListener('click', async () => {
      if (attProof) attProof.textContent = '';
      if (attCopyBtn) attCopyBtn.hidden = true;
      const wallet = requireWallet(attResult);
      if (!wallet) return;
      try {
        setStatus(attResult, 'Signing attestation…', 'info');
        const proof = await signAttestation({
          txid: attTxid ? attTxid.value.trim() : '',
          kind: attKind ? attKind.value : 'opposition',
          rawSubject: attSubject ? attSubject.value : '',
          amount: attAmount ? Number(attAmount.value) : NaN,
          wallet,
        });
        if (attProof) attProof.textContent = JSON.stringify(proof, null, 2);
        if (attCopyBtn) attCopyBtn.hidden = false;
        setStatus(
          attResult,
          'Attestation signed. Paste this into /verify.',
          'ok',
        );
      } catch (e) {
        setStatus(attResult, msgOf(e), 'error');
      }
    });
  }

  if (attCopyBtn) {
    attCopyBtn.addEventListener('click', async () => {
      const text = attProof ? attProof.textContent : '';
      try {
        await navigator.clipboard.writeText(text);
        setStatus(attResult, 'Copied to clipboard.', 'ok');
      } catch {
        setStatus(
          attResult,
          'Could not copy automatically — select the JSON and copy it.',
          'error',
        );
      }
    });
  }

  // Resolve passkey capability, reveal the saved-wallet controls if a wallet is
  // persisted here, and install the shared auto-lock policy so an unlocked key
  // (saved OR ephemeral) is dropped on idle / tab-hide / page-unload. A lock
  // re-renders the controls (and surfaces it on the key status).
  (async () => {
    passkey = await makePasskey({ window: win, rpName });
    session.onLock(() => {
      if (wasUnlocked && keyStatus) {
        setStatus(keyStatus, 'Key locked — cleared from memory.', 'info');
      }
      wasUnlocked = false;
      renderKeyControls().catch(() => {});
    });
    if (doc && win) {
      session.installAutoLock({ document: doc, window: win });
    }
    await renderKeyControls();
  })().catch(() => {});
}

function msgOf(e) {
  return e instanceof Error ? e.message : String(e);
}

// Read the type-specific fields out of the form into the shape buildQuery
// expects. amount is sent as an integer string (grains).
function collectFields(root, type) {
  const val = (sel) => {
    const el = root.querySelector(sel);
    return el ? el.value : '';
  };
  const fields = { amount: val('#field-amount') };
  if (type === 'transfer') {
    fields.address = val('#field-address');
  } else {
    fields.subject = val('#field-subject');
    if (type === 'rescind') {
      fields.kind = val('#field-kind');
    }
  }
  return fields;
}
