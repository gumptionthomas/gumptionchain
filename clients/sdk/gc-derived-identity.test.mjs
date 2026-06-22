import assert from 'node:assert/strict';
import { test } from 'node:test';
import {
  classifyRecognition, makeDerivedIdentity,
} from './gc-derived-identity.mjs';
import { deriveSigningKey } from './gc-derive.mjs';
import { SigningKey } from './gc-signing-key.mjs';

const PRF = Uint8Array.from({ length: 32 }, (_, i) => i + 5);

function fakePasskey() {
  return {
    async isSupported() { return true; },
    async enroll() { return { credentialId: 'cred1', prfOutput: PRF }; },
    async discover() {
      return { credentialId: 'cred1', prfOutput: PRF, userHandle: null };
    },
  };
}

test('enroll: derives a key and returns it with its recovery phrase + credentialId', async () => {
  const id = makeDerivedIdentity({ passkey: fakePasskey() });
  const r = await id.enroll({ userName: 'a' });
  assert.ok(r.address.startsWith('gc1'));
  assert.equal(r.mnemonic.split(' ').length, 24);
  assert.equal(r.credentialId, 'cred1');
  assert.equal(await r.signing_key.address(), r.address);
});

test('resolve: PRF-only identity matches the caller-supplied expected address', async () => {
  const expectedAddress = await (await deriveSigningKey(PRF)).address();
  const id = makeDerivedIdentity({ passkey: fakePasskey() });
  const res = await id.resolve({ expectedAddress });
  assert.equal(res.status, 'ok');
  assert.equal(await res.signing_key.address(), expectedAddress);
});

test('resolve: 2FA identity needs the passphrase; wrong one fails closed', async () => {
  const expectedAddress =
    await (await deriveSigningKey(PRF, { passphrase: 'pw' })).address();
  const id = makeDerivedIdentity({ passkey: fakePasskey() });
  const probe = await id.resolve({ expectedAddress });
  assert.equal(probe.status, 'needs-passphrase');
  const good = await id.resolve({ expectedAddress, passphrase: 'pw' });
  assert.equal(good.status, 'ok');
  const bad = await id.resolve({ expectedAddress, passphrase: 'nope' });
  assert.equal(bad.status, 'mismatch');
  assert.equal(bad.signing_key, undefined);
});

test('resolve: with no expected address, returns the PRF-only derived key', async () => {
  const id = makeDerivedIdentity({ passkey: fakePasskey() });
  const res = await id.resolve({});
  assert.equal(res.status, 'derived');
  assert.equal(
    await res.signing_key.address(),
    await (await deriveSigningKey(PRF)).address(),
  );
});

test('resolve: no passkey discovered -> no-passkey', async () => {
  const id = makeDerivedIdentity({
    passkey: { async discover() { return null; } },
  });
  assert.equal((await id.resolve({})).status, 'no-passkey');
});

// The single-sourced phantom guard (shared by makeOnboarding.recognize() + base
// /signing-key glue). Adopt unless a real-address userHandle contradicts the
// derived address.
test('classifyRecognition: wrap iff userHandle is a real gc address != the derived address', async () => {
  const D = await (await SigningKey.generate()).address();
  const other = await (await SigningKey.generate()).address();
  // random / non-address userHandle -> derived (adopt)
  assert.equal(classifyRecognition({ userHandle: 'not-an-address', derivedAddress: D }), 'derived');
  assert.equal(classifyRecognition({ userHandle: null, derivedAddress: D }), 'derived');
  // a real address that equals the derived address -> derived (PRF backs it)
  assert.equal(classifyRecognition({ userHandle: D, derivedAddress: D }), 'derived');
  // a real address that disagrees with the derived address -> wrap (phantom guard)
  assert.equal(classifyRecognition({ userHandle: other, derivedAddress: D }), 'wrap');
});
