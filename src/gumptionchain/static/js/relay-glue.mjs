// Browser client for the node-proxy RELAY (gumptionchain `node_proxy_blueprint`).
//
// This is the relay counterpart to transact-glue.mjs. transact-glue is the
// DIRECT-to-node path: the browser gc-sig-signs each API request AS the user, so
// the user's address must be a TRANSACTOR on the node. relay-glue is the RELAY
// path for a CLOSED-transactor node: the relay's service key is the only
// authorized caller, and the relay (server-side) gc-sig-signs the node calls.
// The user only signs their OWN transaction payload (proving they hold the key);
// the node validates that signature for balance/ownership. So the browser->relay
// hop here is a plain JSON POST — no GC-* headers.
//
// Flow: build (POST relayBase/txn/<type>) -> sign (the user's key, reusing the
// shared signUnsignedTxn primitive) -> submit (POST relayBase/txn/submit).
//
// Whole-GRIT at the boundary: amounts are sent as `amount_grit`/`denomination_
// grit` (whole/decimal GRIT, max 2 places = one grain); the relay converts to
// grains. normalizeGrit mirrors gumptionchain.units — sub-grain precision is
// rejected client-side before the round-trip.

import { signUnsignedTxn } from '../sdk/gc-transaction.mjs';

// type -> { path on the relay, required body fields }. amount_grit /
// denomination_grit are grit-normalized; `count` is an integer; `kind` is
// constrained for rescind.
const TYPE_SPECS = {
  support: { path: 'support', fields: ['signer', 'subject', 'amount_grit'] },
  oppose: { path: 'oppose', fields: ['signer', 'subject', 'amount_grit'] },
  rescind: {
    path: 'rescind',
    fields: ['signer', 'subject', 'amount_grit', 'kind'],
  },
  transfer: {
    path: 'transfer',
    fields: ['signer', 'to_address', 'amount_grit'],
  },
  split: {
    path: 'split',
    fields: ['signer', 'denomination_grit', 'count'],
  },
};

const GRIT_FIELDS = new Set(['amount_grit', 'denomination_grit']);

// Validate a GRIT amount and return its canonical string. Mirrors
// gumptionchain.units.grit_to_grains: positive, at most one-grain (0.01)
// precision. Throws (fail loud) on a finer-than-grain or non-numeric value
// rather than silently truncating. Returns a string so no float drift crosses
// the wire (the relay parses it with Decimal).
export function normalizeGrit(value, field = 'amount_grit') {
  const str = String(value).trim();
  if (!/^-?\d+(\.\d+)?$/.test(str)) {
    throw new Error(`${field} must be a number`);
  }
  const num = Number(str);
  if (!(num > 0)) {
    throw new Error(`${field} must be positive`);
  }
  const decimals = str.includes('.') ? str.split('.')[1].length : 0;
  if (decimals > 2) {
    throw new Error(`${field} precision finer than one grain (0.01)`);
  }
  // Return the validated decimal string as-is (the regex guarantees a plain
  // [-]digits[.digits] form). Don't round-trip through Number — that would
  // emit exponential notation for very large values; the relay parses the
  // string with Decimal, so leading/trailing zeros are harmless.
  return str;
}

// Build the relay request body for a transaction type from caller-supplied
// fields (using the relay's wire names). Validates required fields, normalizes
// grit amounts, and constrains rescind's kind. Throws on an unknown type or a
// missing/invalid field — before any network call.
export function buildBody(type, fields = {}) {
  const spec = TYPE_SPECS[type];
  if (!spec) {
    throw new Error(`unknown transaction type: ${type}`);
  }
  const body = {};
  for (const name of spec.fields) {
    const value = fields[name];
    if (value === undefined || value === null || value === '') {
      throw new Error(`${type} requires ${name}`);
    }
    if (GRIT_FIELDS.has(name)) {
      body[name] = normalizeGrit(value, name);
    } else if (name === 'count') {
      if (!Number.isInteger(value) || value < 1) {
        throw new Error('count must be a positive integer');
      }
      body[name] = value;
    } else if (name === 'kind') {
      if (value !== 'opposition' && value !== 'support') {
        throw new Error("kind must be 'opposition' or 'support'");
      }
      body[name] = value;
    } else {
      body[name] = value;
    }
  }
  return body;
}

// Join a relay base (origin/prefix the consuming app mounts, e.g. '/relay' or
// 'https://hub.example/relay') with a relay path, without a double slash.
export function relayUrl(relayBase, path) {
  return `${String(relayBase).replace(/\/$/, '')}/${path}`;
}

// Map a relay response to a user-facing message. The relay translates node
// statuses (node 5xx -> 502; node 4xx pass through), so the cases differ from
// the direct-to-node transact-glue: a 403 here means the RELAY's service key is
// not authorized, not the end user's address.
export function relayMessage(status, body = {}, phase = 'submit') {
  const detail =
    body && typeof body === 'object' && typeof body.error === 'string'
      ? body.error
      : '';
  const building = phase === 'build';
  if (status >= 200 && status < 300) {
    return 'Transaction submitted and received by the node.';
  }
  if (status === 403) {
    return (
      'This app is not authorized to submit transactions on your behalf ' +
      '(its relay key is not a TRANSACTOR on the node).'
    );
  }
  if (status === 404) {
    return `Transaction not found${detail ? `: ${detail}` : ''}.`;
  }
  if (status === 429) {
    return 'The relay is rate-limiting submissions. Try again shortly.';
  }
  if (status === 400) {
    return building
      ? `Couldn't build the transaction: ${detail || 'invalid request'}.`
      : `The node rejected the transaction: ${detail || 'validation error'}.`;
  }
  if (status >= 500) {
    return `The node is unavailable (HTTP ${status})${detail ? `: ${detail}` : ''}. Try again shortly.`;
  }
  const lead = building
    ? "Couldn't build the transaction"
    : 'Unexpected response from the relay';
  return `${lead} (HTTP ${status})${detail ? `: ${detail}` : ''}.`;
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

async function postJson(url, payload, fetchImpl) {
  return fetchImpl(url, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(payload),
  });
}

// build: POST relayBase/txn/<type> with the validated body; return the
// node-built UNSIGNED transaction. Throws a mapped message on a non-OK relay
// response (e.g. insufficient funds surfaces here, before anything is signed).
export async function buildUnsigned({
  relayBase,
  type,
  fields,
  fetchImpl = globalThis.fetch,
}) {
  const spec = TYPE_SPECS[type];
  if (!spec) {
    throw new Error(`unknown transaction type: ${type}`);
  }
  const body = buildBody(type, fields);
  const resp = await postJson(relayUrl(relayBase, `txn/${spec.path}`), body, fetchImpl);
  const data = await readBody(resp);
  if (!resp.ok) {
    throw new Error(relayMessage(resp.status, data, 'build'));
  }
  return { unsigned: data };
}

// submit: POST relayBase/txn/submit { signed }; return { txid }. Throws a mapped
// message on a non-OK relay response.
export async function submit({
  relayBase,
  signed,
  fetchImpl = globalThis.fetch,
}) {
  const resp = await postJson(relayUrl(relayBase, 'txn/submit'), { signed }, fetchImpl);
  const data = await readBody(resp);
  if (!resp.ok) {
    throw new Error(relayMessage(resp.status, data, 'submit'));
  }
  return { txid: data.txid };
}

// sign (the user's key) + submit a pre-built unsigned txn. Split from
// buildSignSubmit so an app can confirm with the user between build and sign.
export async function signAndSubmit({
  relayBase,
  unsigned,
  signing_key,
  fetchImpl = globalThis.fetch,
}) {
  const signed = await signUnsignedTxn(unsigned, signing_key);
  const { txid } = await submit({ relayBase, signed, fetchImpl });
  return { unsigned, signed, txid };
}

// The full relay round-trip: build -> sign -> submit. Returns the unsigned and
// signed txns plus the confirmed txid.
export async function buildSignSubmit({
  relayBase,
  type,
  fields,
  signing_key,
  fetchImpl = globalThis.fetch,
}) {
  const { unsigned } = await buildUnsigned({
    relayBase,
    type,
    fields,
    fetchImpl,
  });
  return signAndSubmit({ relayBase, unsigned, signing_key, fetchImpl });
}
