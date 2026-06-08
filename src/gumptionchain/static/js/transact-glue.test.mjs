import { test } from 'node:test';
import assert from 'node:assert/strict';
import {
  buildQuery,
  submitPath,
  responseMessage,
  buildUnsigned,
  signAndSubmit,
  submitSigned,
  encodeSubject,
  signAttestation,
} from './transact-glue.mjs';
import { Wallet } from '../wallet/gc-wallet.mjs';
import { parseStakeAttestation } from '../wallet/gc-attestation.mjs';

// --- buildQuery: one query string, used for BOTH the fetch URL and the
// gc-sig canonical, so it must round-trip exactly the fields the type needs.

test('buildQuery transfer carries public_key, amount, address', () => {
  const q = buildQuery('transfer', {
    publicKey: 'PUB',
    amount: '42',
    address: 'GCdestGC',
  });
  const p = new URLSearchParams(q);
  assert.equal(p.get('public_key'), 'PUB');
  assert.equal(p.get('amount'), '42');
  assert.equal(p.get('address'), 'GCdestGC');
  assert.equal(p.has('subject'), false);
  assert.equal(p.has('kind'), false);
});

test('buildQuery opposition carries public_key, amount, subject (raw)', () => {
  const q = buildQuery('opposition', {
    publicKey: 'PUB',
    amount: '7',
    subject: 'goblins & orcs',
  });
  const p = new URLSearchParams(q);
  assert.equal(p.get('public_key'), 'PUB');
  assert.equal(p.get('amount'), '7');
  // The server takes the RAW subject and encodes it itself — do NOT pre-encode.
  assert.equal(p.get('subject'), 'goblins & orcs');
  assert.equal(p.has('address'), false);
  assert.equal(p.has('kind'), false);
});

test('buildQuery support behaves like opposition', () => {
  const q = buildQuery('support', {
    publicKey: 'PUB',
    amount: '3',
    subject: 'dragons',
  });
  const p = new URLSearchParams(q);
  assert.equal(p.get('subject'), 'dragons');
  assert.equal(p.has('kind'), false);
});

test('buildQuery rescind carries subject AND kind', () => {
  const q = buildQuery('rescind', {
    publicKey: 'PUB',
    amount: '5',
    subject: 'goblins',
    kind: 'support',
  });
  const p = new URLSearchParams(q);
  assert.equal(p.get('subject'), 'goblins');
  assert.equal(p.get('kind'), 'support');
});

test('buildQuery rejects an unknown type', () => {
  assert.throws(() => buildQuery('bogus', {}), /unknown transaction type/);
});

// --- submitPath

test('submitPath builds the /api submit path, encoding the txid segment', () => {
  assert.equal(submitPath('abc123'), '/api/transaction/abc123');
  // A txid with path-significant chars can't reshape the request.
  assert.equal(submitPath('a/b?c'), '/api/transaction/a%2Fb%3Fc');
});

// --- responseMessage: each documented status surfaces distinctly.

test('responseMessage maps success codes', () => {
  for (const s of [200, 201, 202]) {
    const m = responseMessage(s, { received: 'now' });
    assert.match(m, /submitted|accepted|received/i);
  }
});

test('responseMessage 403 is the closed-node message', () => {
  const m = responseMessage(403, {});
  assert.match(m, /not authorized|restricts/i);
});

test('responseMessage 503 is the mempool-full message', () => {
  const m = responseMessage(503, { error: 'mempool full' });
  assert.match(m, /mempool/i);
});

test('responseMessage 400 surfaces the validation error', () => {
  const m = responseMessage(400, { error: 'bad amount' });
  assert.match(m, /bad amount/);
});

test('responseMessage falls back for an unmapped status', () => {
  const m = responseMessage(418, {});
  assert.match(m, /418/);
});

// --- two-step flow: buildUnsigned (GET, verify txid, NO sign) then
// signAndSubmit (sign + POST). A fake wallet tracks sign calls so we can prove
// nothing is signed before the confirm step.

// Note: wallet.sign is used for BOTH the gc-sig request-envelope (on every
// authed request, incl. the build GET) AND the transaction itself. So "not
// signed before confirm" is asserted via: no POST happened, and the built txn
// carries no `signature` — NOT via a sign-call count (which the request auth
// would trip).
function fakeWallet() {
  return {
    address: async () => 'GCsignerGC',
    publicKeyB64: async () => 'SIGNER_PUB',
    sign: async () => 'SIGNATURE_B64',
  };
}

const posts = (calls) => calls.filter((c) => (c.opts.method ?? 'GET') === 'POST');

// Minimal unsigned txn whose self-reported txid actually matches its fields,
// so signUnsignedTxn's honesty check passes. We compute it the same way the
// module does (via the shared gc-transaction txid()).
import { txid as computeTxid } from '../wallet/gc-transaction.mjs';

function unsignedTransfer() {
  return {
    timestamp: '1700000000',
    address: 'GCsignerGC',
    public_key: 'SIGNER_PUB',
    inflows: [{ outflow_txid: 'a'.repeat(64), outflow_idx: 0 }],
    outflows: [{ amount: 42, address: 'GCdestGC' }],
    version: '1',
  };
}

test('buildUnsigned: GET authed, verifies txid, does NOT sign or POST', async () => {
  const unsigned = unsignedTransfer();
  unsigned.txid = await computeTxid({ ...unsigned, txid: undefined });

  const calls = [];
  const fakeFetch = async (url, opts = {}) => {
    calls.push({ url, opts });
    return {
      status: 200,
      ok: true,
      json: async () => unsigned,
      text: async () => JSON.stringify(unsigned),
    };
  };
  const { unsigned: got } = await buildUnsigned({
    type: 'transfer',
    fields: { amount: '42', address: 'GCdestGC' },
    wallet: fakeWallet(),
    nodeHost: 'http://node.example',
    fetchImpl: fakeFetch,
    timestamp: 1700000000,
  });

  // Nothing submitted before confirmation, and the returned txn is UNSIGNED.
  assert.equal(posts(calls).length, 0);
  assert.equal(got.signature, undefined);
  // The one call was the authed build GET.
  assert.equal(calls.length, 1);
  const get = calls[0];
  assert.match(get.url, /\/api\/transaction\/transfer\?/);
  assert.match(get.url, /public_key=SIGNER_PUB/);
  assert.equal(get.opts.headers['GC-Signature'], 'SIGNATURE_B64');
  assert.equal(got.txid, unsigned.txid);
});

test('buildUnsigned rejects a node whose txid does not match its fields', async () => {
  const unsigned = unsignedTransfer();
  unsigned.txid = 'f'.repeat(64); // lie
  const calls = [];
  const fakeFetch = async (url, opts = {}) => {
    calls.push({ url, opts });
    return {
      status: 200,
      ok: true,
      json: async () => unsigned,
      text: async () => JSON.stringify(unsigned),
    };
  };
  await assert.rejects(
    () =>
      buildUnsigned({
        type: 'transfer',
        fields: { amount: '42', address: 'GCdestGC' },
        wallet: fakeWallet(),
        nodeHost: 'http://node.example',
        fetchImpl: fakeFetch,
        timestamp: 1700000000,
      }),
    /txid mismatch/,
  );
  assert.equal(posts(calls).length, 0); // a dishonest txn is never submitted
});

test('buildUnsigned surfaces a build-GET error without signing/POSTing', async () => {
  const calls = [];
  const fakeFetch = async (url, opts = {}) => {
    calls.push({ url, opts });
    return {
      status: 403,
      ok: false,
      json: async () => ({ error: 'forbidden' }),
      text: async () => '{"error":"forbidden"}',
    };
  };
  await assert.rejects(
    () =>
      buildUnsigned({
        type: 'transfer',
        fields: { amount: '1', address: 'GCdestGC' },
        wallet: fakeWallet(),
        nodeHost: 'http://node.example',
        fetchImpl: fakeFetch,
        timestamp: 1700000000,
      }),
    /not authorized|restricts/i,
  );
  assert.equal(calls.length, 1); // only the GET
  assert.equal(posts(calls).length, 0); // nothing submitted
});

test('signAndSubmit: signs the confirmed txn and POSTs it', async () => {
  const unsigned = unsignedTransfer();
  unsigned.txid = await computeTxid({ ...unsigned, txid: undefined });

  const calls = [];
  const fakeFetch = async (url, opts = {}) => {
    calls.push({ url, opts });
    return {
      status: 201,
      ok: true,
      json: async () => ({ received: 'now' }),
      text: async () => '{"received":"now"}',
    };
  };
  const result = await signAndSubmit({
    unsigned,
    wallet: fakeWallet(),
    nodeHost: 'http://node.example',
    fetchImpl: fakeFetch,
  });

  // Exactly one call — the submit POST carrying the now-signed txn.
  assert.equal(calls.length, 1);
  const post = calls[0];
  assert.equal(post.url, submitPath(unsigned.txid));
  assert.equal(post.opts.method, 'POST');
  const body = JSON.parse(post.opts.body);
  assert.equal(body.signature, 'SIGNATURE_B64');
  assert.equal(body.txid, unsigned.txid);
  assert.equal(post.opts.headers['GC-Signature'], 'SIGNATURE_B64');
  assert.equal(result.status, 201);
  assert.match(result.message, /submitted|accepted|received/i);
});

// --- encodeSubject: must produce the base64url, padding-stripped form that
// Python's encode_subject produces, since /verify compares the claim's subject
// against on-chain provenance (which is the ENCODED form). The literals here
// are locked to a Python pytest (tests/test_encode_subject_parity.py).

test('encodeSubject matches Python encode_subject for ascii', () => {
  assert.equal(encodeSubject('goblins'), 'Z29ibGlucw');
});

test('encodeSubject matches Python for a value with a space', () => {
  assert.equal(encodeSubject('cancel me'), 'Y2FuY2VsIG1l');
});

test('encodeSubject matches Python for a multi-byte (UTF-8) value', () => {
  assert.equal(encodeSubject('café'), 'Y2Fmw6k');
});

test('encodeSubject is base64url with no padding (no +, /, =)', () => {
  const enc = encodeSubject('the quick brown fox????');
  assert.equal(/[+/=]/.test(enc), false);
});

// --- signAttestation: encodes the RAW subject, builds a claim with the ENCODED
// subject, signs it, and round-trips through parseStakeAttestation.

test('signAttestation builds a claim with the ENCODED subject', async () => {
  const wallet = await Wallet.generate();
  const proof = await signAttestation({
    txid: '1'.repeat(64),
    kind: 'opposition',
    rawSubject: 'goblins',
    amount: 300,
    wallet,
    timestamp: '1700002000',
  });
  // The signed message's subject is the encoded form, not the raw input.
  const claim = parseStakeAttestation(proof);
  assert.equal(claim.subject, 'Z29ibGlucw');
  assert.notEqual(claim.subject, 'goblins');
  assert.equal(claim.txid, '1'.repeat(64));
  assert.equal(claim.kind, 'opposition');
  assert.equal(claim.amount, 300);
  // The proof is a gc-msg-v1 envelope signed by this wallet.
  assert.equal(proof.scheme, 'gc-msg-v1');
  assert.equal(proof.address, await wallet.address());
});

test('signAttestation supports the support kind', async () => {
  const wallet = await Wallet.generate();
  const proof = await signAttestation({
    txid: '2'.repeat(64),
    kind: 'support',
    rawSubject: 'cancel me',
    amount: 5,
    wallet,
  });
  const claim = parseStakeAttestation(proof);
  assert.equal(claim.kind, 'support');
  assert.equal(claim.subject, 'Y2FuY2VsIG1l');
});

test('submitSigned rejects a pasted object missing txid/signature', async () => {
  let fetched = false;
  const fakeFetch = async () => {
    fetched = true;
    return { status: 201, ok: true, text: async () => '{}' };
  };
  await assert.rejects(
    () =>
      submitSigned({
        signed: { outflows: [] }, // valid JSON, but not a signed txn
        wallet: fakeWallet(),
        nodeHost: 'http://node.example',
        fetchImpl: fakeFetch,
      }),
    /signed transaction|txid or signature/i,
  );
  assert.equal(fetched, false); // never POSTed to /api/transaction/undefined
});
