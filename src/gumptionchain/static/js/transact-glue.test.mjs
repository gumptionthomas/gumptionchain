import { test } from 'node:test';
import assert from 'node:assert/strict';
import {
  buildQuery,
  submitPath,
  responseMessage,
  buildSignSubmit,
} from './transact-glue.mjs';

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

// --- buildSignSubmit happy path: a fake wallet + fake fetch verify that the
// GET is gc-sig authed, the returned unsigned txn is signed, and the POST body
// is the signed txn carrying a signature + GC-* headers.

function fakeWallet() {
  return {
    address: async () => 'GCsignerGC',
    publicKeyB64: async () => 'SIGNER_PUB',
    sign: async () => 'SIGNATURE_B64',
  };
}

// Minimal unsigned txn whose self-reported txid actually matches its fields,
// so signUnsignedTxn's honesty check passes. We compute it the same way the
// module does (via the shared gc-transaction txid()).
import { txid as computeTxid } from '../wallet/gc-transaction.mjs';

test('buildSignSubmit: GET authed -> sign -> POST signed txn', async () => {
  const unsigned = {
    timestamp: '1700000000',
    address: 'GCsignerGC',
    public_key: 'SIGNER_PUB',
    inflows: [{ outflow_txid: 'a'.repeat(64), outflow_idx: 0 }],
    outflows: [{ amount: 42, address: 'GCdestGC' }],
    version: '1',
  };
  unsigned.txid = await computeTxid({ ...unsigned, txid: undefined });

  const calls = [];
  const fakeFetch = async (url, opts = {}) => {
    calls.push({ url, opts });
    if (opts.method === undefined || opts.method === 'GET') {
      return {
        status: 200,
        ok: true,
        json: async () => unsigned,
        text: async () => JSON.stringify(unsigned),
      };
    }
    return {
      status: 201,
      ok: true,
      json: async () => ({ received: 'now' }),
      text: async () => '{"received":"now"}',
    };
  };

  const result = await buildSignSubmit({
    type: 'transfer',
    fields: { amount: '42', address: 'GCdestGC' },
    wallet: fakeWallet(),
    nodeHost: 'http://node.example',
    fetchImpl: fakeFetch,
    timestamp: 1700000000,
  });

  // Two calls: the build GET then the submit POST.
  assert.equal(calls.length, 2);
  const get = calls[0];
  const post = calls[1];

  // GET went to the build endpoint with the public_key in the query and was
  // gc-sig authed.
  assert.match(get.url, /\/api\/transaction\/transfer\?/);
  assert.match(get.url, /public_key=SIGNER_PUB/);
  assert.equal(get.opts.headers['GC-Address'], 'GCsignerGC');
  assert.equal(get.opts.headers['GC-Signature'], 'SIGNATURE_B64');
  assert.equal(get.opts.headers['GC-Sig-Version'], '1');

  // POST went to the submit path, body is the signed txn, gc-sig authed.
  assert.equal(post.url, submitPath(unsigned.txid));
  assert.equal(post.opts.method, 'POST');
  const body = JSON.parse(post.opts.body);
  assert.equal(body.signature, 'SIGNATURE_B64');
  assert.equal(body.txid, unsigned.txid);
  assert.equal(post.opts.headers['GC-Signature'], 'SIGNATURE_B64');

  // The result surfaces the success status + parsed unsigned txn (for the
  // confirmation UX) + a user-facing message.
  assert.equal(result.status, 201);
  assert.equal(result.unsigned.txid, unsigned.txid);
  assert.match(result.message, /submitted|accepted|received/i);
});

test('buildSignSubmit surfaces a build-GET error without POSTing', async () => {
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
      buildSignSubmit({
        type: 'transfer',
        fields: { amount: '1', address: 'GCdestGC' },
        wallet: fakeWallet(),
        nodeHost: 'http://node.example',
        fetchImpl: fakeFetch,
        timestamp: 1700000000,
      }),
    /not authorized|restricts/i,
  );
  // Only the GET happened — no signed POST after an auth failure.
  assert.equal(calls.length, 1);
});
