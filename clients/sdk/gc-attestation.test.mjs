import { test } from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { SigningKey } from './gc-signing-key.mjs';
import {
  buildStakeMessage, signStakeAttestation, parseStakeAttestation,
  verifyStake, BadAttestationError,
} from './gc-attestation.mjs';

const TS = '1700002000';
// A canonical txid is a 64-char lowercase-hex mill hash.
const TX = '1'.repeat(64);
const CLAIM = { txid: TX, kind: 'opposition', subject: 'goblins', amount: 300 };

// A provenance object shaped like the GET /transaction/<txid> response.
function provenanceFor(address, { status = 'canonical', confirmations = 3 } = {}) {
  return {
    txid: TX, address, status, confirmations,
    outflows: [
      { kind: 'opposition', subject: 'goblins', amount: 300 },
      { kind: 'transfer', address: 'GCchangeGC', amount: 9700 },
    ],
  };
}

test('buildStakeMessage uses fixed key order, omits absent optionals', () => {
  assert.equal(
    buildStakeMessage(CLAIM),
    `{"txid":"${TX}","kind":"opposition","subject":"goblins","amount":300}`,
  );
  assert.equal(
    buildStakeMessage({ ...CLAIM, handle: 'me.bsky.social' }),
    `{"txid":"${TX}","kind":"opposition","subject":"goblins","amount":300,`
    + '"handle":"me.bsky.social"}',
  );
  assert.equal(
    buildStakeMessage({ txid: TX, kind: 'transfer', address: 'GCxGC', amount: 5 }),
    `{"txid":"${TX}","kind":"transfer","address":"GCxGC","amount":5}`,
  );
});

test('buildStakeMessage rejects malformed claims', () => {
  assert.throws(() => buildStakeMessage({ kind: 'opposition', subject: 's', amount: 1 }), BadAttestationError);
  assert.throws(() => buildStakeMessage({ txid: TX, kind: 'nope', subject: 's', amount: 1 }), BadAttestationError);
  assert.throws(() => buildStakeMessage({ txid: TX, kind: 'opposition', subject: 's', amount: 0 }), BadAttestationError);
  assert.throws(() => buildStakeMessage({ txid: TX, kind: 'opposition', address: 'a', amount: 1 }), BadAttestationError);
  assert.throws(() => buildStakeMessage({ txid: TX, kind: 'transfer', subject: 's', amount: 1 }), BadAttestationError);
});

test('buildStakeMessage rejects a malformed txid', () => {
  // Not 64-char lowercase hex -> rejected up front (see #187), so a malformed
  // txid can't slip through to a provenance fetch and be mis-read as not-found.
  for (const bad of [
    'tx1', 'g'.repeat(64), '1'.repeat(63), '1'.repeat(65),
    'A'.repeat(64), `${'1'.repeat(63)}/`,
  ]) {
    assert.throws(() => buildStakeMessage({ ...CLAIM, txid: bad }), BadAttestationError);
  }
  assert.ok(
    buildStakeMessage({ ...CLAIM, txid: 'a'.repeat(64) })
      .startsWith(`{"txid":"${'a'.repeat(64)}"`),
  );
});

test('sign -> parse round-trips the claim', async () => {
  const w = await SigningKey.generate();
  const proof = await signStakeAttestation(w, CLAIM, { timestamp: TS });
  assert.equal(proof.scheme, 'gc-msg-v1');
  assert.deepEqual(parseStakeAttestation(proof), CLAIM);
});

test('parseStakeAttestation throws on a non-claim message', () => {
  assert.throws(
    () => parseStakeAttestation({ message: 'not json' }),
    BadAttestationError,
  );
});

test('parseStakeAttestation rejects non-canonical encodings', () => {
  // float amount (JSON.parse coerces 300.0 -> 300; rebuild differs)
  assert.throws(() => parseStakeAttestation({
    message:
      `{"txid":"${TX}","kind":"opposition","subject":"goblins","amount":300.0}`,
  }), BadAttestationError);
  // reordered keys
  assert.throws(() => parseStakeAttestation({
    message:
      `{"kind":"opposition","txid":"${TX}","subject":"goblins","amount":300}`,
  }), BadAttestationError);
});

test('buildStakeMessage rejects a present off-side key (even null)', () => {
  assert.throws(() => buildStakeMessage(
    { txid: TX, kind: 'transfer', address: 'a', amount: 1, subject: null },
  ), BadAttestationError);
  assert.throws(() => buildStakeMessage(
    { txid: TX, kind: 'opposition', subject: 's', amount: 1, address: null },
  ), BadAttestationError);
});

test('verifyStake wraps a malformed proof envelope as BadAttestationError', async () => {
  const w = await SigningKey.generate();
  const proof = await signStakeAttestation(w, CLAIM, { timestamp: TS });
  proof.scheme = 'gc-sig-v1'; // malformed gc-msg-v1 envelope
  await assert.rejects(
    () => verifyStake(proof, { fetchProvenance: async () => provenanceFor(await w.address()) }),
    BadAttestationError,
  );
});

test('verifyStake valid when signature + onchain + consistent all hold', async () => {
  const w = await SigningKey.generate();
  const proof = await signStakeAttestation(w, CLAIM, { timestamp: TS });
  const fetchProvenance = async () => provenanceFor(await w.address());
  const v = await verifyStake(proof, { fetchProvenance });
  assert.equal(v.valid, true);
  assert.deepEqual(v.checks, { signature: true, onchain: true, consistent: true });
  assert.equal(v.signer, await w.address());
  assert.equal(v.confirmations, 3);
  assert.deepEqual(v.reasons, []);
});

test('verifyStake reports bad-signature on a tampered claim', async () => {
  const w = await SigningKey.generate();
  const proof = await signStakeAttestation(w, CLAIM, { timestamp: TS });
  proof.message = buildStakeMessage({ ...CLAIM, amount: 999 });
  const v = await verifyStake(proof, { fetchProvenance: async () => provenanceFor(await w.address()) });
  assert.equal(v.valid, false);
  assert.ok(v.reasons.includes('bad-signature'));
});

test('verifyStake reports txn-not-found, not-canonical, insufficient-confirmations', async () => {
  const w = await SigningKey.generate();
  const proof = await signStakeAttestation(w, CLAIM, { timestamp: TS });
  const addr = await w.address();

  const missing = await verifyStake(proof, { fetchProvenance: async () => null });
  assert.equal(missing.valid, false);
  assert.ok(missing.reasons.includes('txn-not-found'));

  const orphaned = await verifyStake(proof, {
    fetchProvenance: async () => provenanceFor(addr, { status: 'orphaned' }),
  });
  assert.ok(orphaned.reasons.includes('not-canonical'));

  const shallow = await verifyStake(proof, {
    fetchProvenance: async () => provenanceFor(addr, { confirmations: 1 }),
    minConfirmations: 6,
  });
  assert.ok(shallow.reasons.includes('insufficient-confirmations'));
});

test('verifyStake reports signer-not-staker and claim-mismatch', async () => {
  const w = await SigningKey.generate();
  const proof = await signStakeAttestation(w, CLAIM, { timestamp: TS });

  const notStaker = await verifyStake(proof, {
    fetchProvenance: async () => provenanceFor('GCsomeoneelseGC'),
  });
  assert.ok(notStaker.reasons.includes('signer-not-staker'));

  const addr = await w.address();
  const mismatch = await verifyStake(proof, {
    fetchProvenance: async () => ({
      txid: TX, address: addr, status: 'canonical', confirmations: 3,
      outflows: [{ kind: 'opposition', subject: 'orcs', amount: 300 }],
    }),
  });
  assert.ok(mismatch.reasons.includes('claim-mismatch'));
});

test('JS canonical messages match the committed golden vectors', () => {
  const vec = JSON.parse(readFileSync(
    new URL('./testdata/gc-attestation-vectors.json', import.meta.url),
  ));
  for (const c of vec) {
    assert.equal(buildStakeMessage(c.claim), c.message);
  }
});
