import { test } from 'node:test';
import assert from 'node:assert/strict';

import {
  buildBody,
  normalizeGrit,
  relayMessage,
  relayUrl,
  buildUnsigned,
  submit,
  buildSignSubmit,
} from './relay-glue.mjs';
import { SigningKey } from '../sdk/gc-signing-key.mjs';
import { txid as computeTxid } from '../sdk/gc-transaction.mjs';

const SIGNER = 'gc13sqcx509fwu3mkceq4hmll0xcufszhzajp8seuvdy22z872tn6sqxlqndc';

// A fetch stand-in: records the call, returns a fake Response (.ok/.status/
// .text()). One per assertion so we can inspect the request it received.
function fakeFetch(status, body) {
  const calls = [];
  const fn = (url, init) => {
    calls.push({ url, init });
    return Promise.resolve({
      ok: status >= 200 && status < 300,
      status,
      text: () => Promise.resolve(JSON.stringify(body)),
    });
  };
  fn.calls = calls;
  return fn;
}

// --- buildBody: type -> relay request body, validated + grit-normalized ------

test('buildBody support/oppose carry signer, subject, amount_grit', () => {
  for (const type of ['support', 'oppose']) {
    const body = buildBody(type, {
      signer: SIGNER,
      subject: 'pineapple belongs on pizza',
      amount_grit: 5,
    });
    assert.deepEqual(body, {
      signer: SIGNER,
      subject: 'pineapple belongs on pizza',
      amount_grit: '5',
    });
  }
});

test('buildBody rescind also carries kind', () => {
  const body = buildBody('rescind', {
    signer: SIGNER,
    subject: 'x',
    amount_grit: 1,
    kind: 'support',
  });
  assert.equal(body.kind, 'support');
});

test('buildBody transfer carries to_address; split carries denomination+count', () => {
  const t = buildBody('transfer', {
    signer: SIGNER,
    to_address: SIGNER,
    amount_grit: 2,
  });
  assert.equal(t.to_address, SIGNER);
  const s = buildBody('split', {
    signer: SIGNER,
    denomination_grit: 1,
    count: 4,
  });
  assert.deepEqual(s, {
    signer: SIGNER,
    denomination_grit: '1',
    count: 4,
  });
});

test('buildBody rejects an unknown type', () => {
  assert.throws(() => buildBody('mint', { signer: SIGNER }), /unknown.*type/i);
});

test('buildBody rejects a missing required field', () => {
  assert.throws(
    () => buildBody('support', { signer: SIGNER, amount_grit: 1 }),
    /subject/,
  );
});

test('buildBody rejects a bad rescind kind', () => {
  assert.throws(
    () =>
      buildBody('rescind', {
        signer: SIGNER,
        subject: 'x',
        amount_grit: 1,
        kind: 'nope',
      }),
    /kind/,
  );
});

// --- normalizeGrit: whole-GRIT boundary, sub-grain rejected (mirror units) ---

test('normalizeGrit accepts whole + 2-decimal amounts (exact)', () => {
  assert.equal(normalizeGrit(5), '5');
  assert.equal(normalizeGrit('1.5'), '1.5');
  assert.equal(normalizeGrit('0.07'), '0.07');
  assert.equal(normalizeGrit(12.34), '12.34');
});

test('normalizeGrit rejects sub-grain precision', () => {
  assert.throws(() => normalizeGrit('0.001'), /grain/i);
});

test('normalizeGrit rejects non-positive and non-numeric', () => {
  assert.throws(() => normalizeGrit(0), /positive/i);
  assert.throws(() => normalizeGrit(-1), /positive/i);
  assert.throws(() => normalizeGrit('abc'), /number|amount/i);
});

// --- relayMessage: relay status -> user-facing string ------------------------

test('relayMessage maps success', () => {
  assert.match(relayMessage(200, { txid: 't' }), /submitted|received/i);
});

test('relayMessage 403 is the relay-not-authorized message', () => {
  assert.match(relayMessage(403, { error: '...' }), /authorized|relay/i);
});

test('relayMessage 400 surfaces the relay error detail', () => {
  assert.match(relayMessage(400, { error: 'invalid subject' }), /invalid subject/);
});

test('relayMessage 404 is unknown-txid', () => {
  assert.match(relayMessage(404, { error: 'unknown txid' }), /unknown|not found/i);
});

// --- relayUrl: joins base + path without a double slash ----------------------

test('relayUrl joins base and path, trimming a trailing slash', () => {
  assert.equal(relayUrl('/relay', 'txn/support'), '/relay/txn/support');
  assert.equal(relayUrl('/relay/', 'txn/submit'), '/relay/txn/submit');
});

// --- buildUnsigned: POST relayBase/txn/<type>, return { unsigned } -----------

test('buildUnsigned POSTs the body to the relay and returns the unsigned txn', async () => {
  const unsigned = { txid: 't1', outflows: [{ amount: 500, support: 'enc' }] };
  const fetchImpl = fakeFetch(200, unsigned);
  const out = await buildUnsigned({
    relayBase: '/relay',
    type: 'support',
    fields: { signer: SIGNER, subject: 'goblins', amount_grit: 5 },
    fetchImpl,
  });
  assert.deepEqual(out, { unsigned });
  const { url, init } = fetchImpl.calls[0];
  assert.equal(url, '/relay/txn/support');
  assert.equal(init.method, 'POST');
  assert.equal(JSON.parse(init.body).amount_grit, '5');
});

test('buildUnsigned throws a mapped message on a non-OK relay response', async () => {
  const fetchImpl = fakeFetch(403, { error: 'relay key not authorized' });
  await assert.rejects(
    buildUnsigned({
      relayBase: '/relay',
      type: 'support',
      fields: { signer: SIGNER, subject: 'g', amount_grit: 1 },
      fetchImpl,
    }),
    /authorized|relay/i,
  );
});

// --- submit: POST relayBase/txn/submit { signed } -> { txid } ----------------

test('submit POSTs the signed txn and returns the txid', async () => {
  const fetchImpl = fakeFetch(200, { txid: 'abc123' });
  const out = await submit({
    relayBase: '/relay',
    signed: { txid: 'abc123', signature: 'sig', address: SIGNER },
    fetchImpl,
  });
  assert.deepEqual(out, { txid: 'abc123' });
  const { url, init } = fetchImpl.calls[0];
  assert.equal(url, '/relay/txn/submit');
  assert.equal(JSON.parse(init.body).signed.txid, 'abc123');
});

// --- buildSignSubmit: end-to-end build -> sign (real key) -> submit ----------

test('buildSignSubmit signs the unsigned txn with the user key and submits', async () => {
  const key = await SigningKey.generate();
  const address = await key.address();
  // A node-built unsigned txn with a correct txid (so signUnsignedTxn agrees).
  const draft = {
    timestamp: '2026-06-27T00:00:00Z',
    address,
    inflows: [{ outflow_txid: 'a'.repeat(64), outflow_idx: 0 }],
    outflows: [{ amount: 500, support: 'enc' }],
    version: '2',
  };
  const unsigned = { ...draft, txid: await computeTxid(draft) };

  // build returns the unsigned txn; submit returns the txid.
  let call = 0;
  const fetchImpl = (url, init) => {
    call += 1;
    const body = call === 1 ? unsigned : { txid: unsigned.txid };
    fetchImpl.last = { url, init };
    return Promise.resolve({
      ok: true,
      status: 200,
      text: () => Promise.resolve(JSON.stringify(body)),
    });
  };

  const out = await buildSignSubmit({
    relayBase: '/relay',
    type: 'support',
    fields: { signer: address, subject: 'goblins', amount_grit: 5 },
    signing_key: key,
    fetchImpl,
  });

  assert.equal(out.txid, unsigned.txid);
  assert.equal(out.signed.address, address);
  assert.ok(out.signed.signature, 'the signed txn carries a signature');
  // last call was the submit, carrying the signed txn.
  assert.equal(fetchImpl.last.url, '/relay/txn/submit');
  assert.equal(JSON.parse(fetchImpl.last.init.body).signed.signature, out.signed.signature);
});
