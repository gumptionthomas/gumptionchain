import { test } from 'node:test';
import assert from 'node:assert/strict';
import { makeWebauthnPasskey } from './gc-passkey-webauthn.mjs';
import { makeIdbStore } from './gc-store-idb.mjs';

test('webauthn passkey adapter exposes the passkey interface', async () => {
  const pk = makeWebauthnPasskey({ rpId: 'example.com', rpName: 'Demo' });
  for (const m of ['isSupported', 'enroll', 'unlock']) {
    assert.equal(typeof pk[m], 'function');
  }
  // In Node (no window) isSupported() must resolve false, not throw.
  assert.equal(await pk.isSupported(), false);
});

test('idb store adapter exposes the store interface', () => {
  const store = makeIdbStore({ dbName: 'gc-signing-key-test' });
  for (const m of ['get', 'put', 'delete']) {
    assert.equal(typeof store[m], 'function');
  }
});
