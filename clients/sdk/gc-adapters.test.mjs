import { test } from 'node:test';
import assert from 'node:assert/strict';
import { makeWebauthnPasskey, recognize } from './gc-passkey-webauthn.mjs';
import { makeIdbStore } from './gc-store-idb.mjs';

test('webauthn passkey adapter exposes the passkey interface', async () => {
  const pk = makeWebauthnPasskey({ rpId: 'example.com', rpName: 'Demo' });
  for (const m of ['isSupported', 'enroll', 'unlock', 'discover', 'isConditionalAvailable']) {
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

// --- discover(): the real WebAuthn path, exercised via a minimal global fake.
// (enroll/unlock remain manual per MANUAL-VERIFICATION.md; discover carries
// enough logic — empty allowCredentials, PRF extraction, null-on-dismissal,
// mediation forwarding — to warrant a unit test.)

function fakeAssertion({
  rawId = new Uint8Array([1, 2, 3]),
  prf = new Uint8Array(32).fill(9),
  userHandle = 'GCdemoaddrGC',
} = {}) {
  return {
    rawId,
    response: {
      // Real browsers expose userHandle as ArrayBuffer | null — use .buffer so
      // the mock matches that shape (TextDecoder handles either).
      userHandle: userHandle == null ? null : new TextEncoder().encode(userHandle).buffer,
    },
    getClientExtensionResults: () => (prf ? { prf: { results: { first: prf } } } : {}),
  };
}

// Use property descriptors, not plain assignment: Node 21+ defines `navigator`
// as a non-writable global, so `globalThis.navigator = …` can throw.
function setGlobal(name, value) {
  const prev = Object.getOwnPropertyDescriptor(globalThis, name);
  if (value === undefined) {
    delete globalThis[name];
  } else {
    Object.defineProperty(globalThis, name, { configurable: true, writable: true, value });
  }
  return () => {
    if (prev) Object.defineProperty(globalThis, name, prev);
    else delete globalThis[name];
  };
}

async function withFakeWebauthn({ getImpl, conditional }, fn) {
  const calls = [];
  const restoreNav = setGlobal('navigator', {
    credentials: { get: async (opts) => { calls.push(opts); return getImpl(opts); } },
  });
  let restoreWin = () => {};
  if (conditional !== undefined) {
    const PKC = function () {};
    PKC.isConditionalMediationAvailable = async () => conditional;
    restoreWin = setGlobal('window', { PublicKeyCredential: PKC });
  }
  try {
    return await fn(calls);
  } finally {
    restoreWin();
    restoreNav();
  }
}

test('discover() returns {credentialId, prfOutput}; empty allowCredentials + PRF eval + forwarded mediation', async () => {
  await withFakeWebauthn({ getImpl: async () => fakeAssertion() }, async (calls) => {
    const pk = makeWebauthnPasskey({ rpId: 'gumption.com', rpName: 'G' });
    const r = await pk.discover({ mediation: 'conditional' });
    assert.equal(typeof r.credentialId, 'string');
    assert.ok(r.prfOutput instanceof Uint8Array);
    assert.deepEqual(calls[0].publicKey.allowCredentials, []);
    assert.ok(calls[0].publicKey.extensions.prf.eval.first);
    assert.equal(calls[0].mediation, 'conditional');
  });
});

test('discover() returns the credential userHandle (the enrolled GC address)', async () => {
  await withFakeWebauthn({ getImpl: async () => fakeAssertion({ userHandle: 'GCalice123GC' }) }, async () => {
    const pk = makeWebauthnPasskey({ rpId: 'gumption.com', rpName: 'G' });
    const r = await pk.discover();
    assert.equal(r.userHandle, 'GCalice123GC');
  });
});

test('discover() userHandle is null when the assertion carries none', async () => {
  await withFakeWebauthn({ getImpl: async () => fakeAssertion({ userHandle: null }) }, async () => {
    const pk = makeWebauthnPasskey({ rpId: 'gumption.com', rpName: 'G' });
    const r = await pk.discover();
    assert.equal(r.userHandle, null);
  });
});

test('discover() returns null when the user dismisses (NotAllowedError)', async () => {
  await withFakeWebauthn({
    getImpl: async () => { const e = new Error('dismissed'); e.name = 'NotAllowedError'; throw e; },
  }, async () => {
    const pk = makeWebauthnPasskey({ rpId: 'gumption.com', rpName: 'G' });
    assert.equal(await pk.discover(), null);
  });
});

test('discover() returns null when aborted (AbortError)', async () => {
  await withFakeWebauthn({
    getImpl: async () => { const e = new Error('aborted'); e.name = 'AbortError'; throw e; },
  }, async () => {
    const pk = makeWebauthnPasskey({ rpId: 'gumption.com', rpName: 'G' });
    assert.equal(await pk.discover(), null);
  });
});

test('discover() returns null when navigator is unavailable', async () => {
  const restore = setGlobal('navigator', undefined);
  try {
    const pk = makeWebauthnPasskey({ rpId: 'gumption.com', rpName: 'G' });
    assert.equal(await pk.discover(), null);
  } finally {
    restore();
  }
});

test('discover() throws UnsupportedError when the assertion has no PRF', async () => {
  await withFakeWebauthn({ getImpl: async () => fakeAssertion({ prf: null }) }, async () => {
    const pk = makeWebauthnPasskey({ rpId: 'gumption.com', rpName: 'G' });
    await assert.rejects(() => pk.discover(), /PRF/);
  });
});

// --- recognize(): thin wrapper over discover() → { recognized, address } (#315)

test('recognize() returns recognized+address when a passkey carries a userHandle', async () => {
  await withFakeWebauthn(
    { getImpl: async () => fakeAssertion({ userHandle: 'gc1alice' }) },
    async () => {
      const r = await recognize({ rpId: 'gumption.com' });
      assert.deepEqual(r, { recognized: true, address: 'gc1alice' });
    },
  );
});

test('recognize() forwards mediation and signal to the discovery call', async () => {
  let seen = null;
  await withFakeWebauthn(
    { getImpl: async (opts) => { seen = opts; return fakeAssertion({ userHandle: 'gc1bob' }); } },
    async () => {
      const signal = new AbortController().signal;
      const r = await recognize({ rpId: 'gumption.com', mediation: 'conditional', signal });
      assert.equal(r.recognized, true);
      assert.equal(seen.mediation, 'conditional');
      assert.equal(seen.signal, signal);
    },
  );
});

test('recognize() is false+null when the user dismisses (NotAllowedError)', async () => {
  await withFakeWebauthn(
    { getImpl: async () => { const e = new Error('no'); e.name = 'NotAllowedError'; throw e; } },
    async () => {
      assert.deepEqual(await recognize({ rpId: 'gumption.com' }), { recognized: false, address: null });
    },
  );
});

test('recognize() is false+null when navigator is unavailable', async () => {
  const restore = setGlobal('navigator', undefined);
  try {
    assert.deepEqual(await recognize({ rpId: 'gumption.com' }), { recognized: false, address: null });
  } finally {
    restore();
  }
});

test('recognize() is false+null when a credential carries no userHandle', async () => {
  await withFakeWebauthn(
    { getImpl: async () => fakeAssertion({ userHandle: null }) },
    async () => {
      assert.deepEqual(await recognize({ rpId: 'gumption.com' }), { recognized: false, address: null });
    },
  );
});

test('recognize() defaults mediation to "optional"', async () => {
  let seen = null;
  await withFakeWebauthn(
    { getImpl: async (opts) => { seen = opts; return fakeAssertion({ userHandle: 'gc1dave' }); } },
    async () => {
      await recognize({ rpId: 'gumption.com' });
      assert.equal(seen.mediation, 'optional');
    },
  );
});

test('recognize() is false+null when the assertion has no PRF (UnsupportedError)', async () => {
  await withFakeWebauthn(
    { getImpl: async () => fakeAssertion({ prf: null }) },
    async () => {
      assert.deepEqual(await recognize({ rpId: 'gumption.com' }), { recognized: false, address: null });
    },
  );
});

test('recognize() is false+null when discovery is aborted (AbortError)', async () => {
  await withFakeWebauthn(
    { getImpl: async () => { const e = new Error('aborted'); e.name = 'AbortError'; throw e; } },
    async () => {
      const ac = new AbortController();
      ac.abort();
      assert.deepEqual(
        await recognize({ rpId: 'gumption.com', signal: ac.signal }),
        { recognized: false, address: null },
      );
    },
  );
});

test('recognize() called with no arguments is false+null (unsupported navigator)', async () => {
  const restore = setGlobal('navigator', undefined);
  try {
    assert.deepEqual(await recognize(), { recognized: false, address: null });
  } finally {
    restore();
  }
});

test('isConditionalAvailable() reflects platform support and never throws', async () => {
  await withFakeWebauthn({ getImpl: async () => null, conditional: true }, async () => {
    const pk = makeWebauthnPasskey({ rpId: 'gumption.com', rpName: 'G' });
    assert.equal(await pk.isConditionalAvailable(), true);
  });
  await withFakeWebauthn({ getImpl: async () => null, conditional: false }, async () => {
    const pk = makeWebauthnPasskey({ rpId: 'gumption.com', rpName: 'G' });
    assert.equal(await pk.isConditionalAvailable(), false);
  });
  const restore = setGlobal('window', undefined);
  try {
    const pk = makeWebauthnPasskey({ rpId: 'gumption.com', rpName: 'G' });
    assert.equal(await pk.isConditionalAvailable(), false);
  } finally {
    restore();
  }
});

test('discover() returns null when navigator.credentials.get is missing', async () => {
  const restore = setGlobal('navigator', { credentials: {} });
  try {
    const pk = makeWebauthnPasskey({ rpId: 'gumption.com', rpName: 'G' });
    assert.equal(await pk.discover(), null);
  } finally {
    restore();
  }
});

test('discover() returns null on SecurityError (insecure context)', async () => {
  await withFakeWebauthn({
    getImpl: async () => { const e = new Error('insecure'); e.name = 'SecurityError'; throw e; },
  }, async () => {
    const pk = makeWebauthnPasskey({ rpId: 'gumption.com', rpName: 'G' });
    assert.equal(await pk.discover(), null);
  });
});

test('discover() omits signal when it is null or undefined', async () => {
  await withFakeWebauthn({ getImpl: async () => fakeAssertion() }, async (calls) => {
    const pk = makeWebauthnPasskey({ rpId: 'gumption.com', rpName: 'G' });
    await pk.discover({ signal: null });
    assert.ok(!('signal' in calls[0]));
  });
});

test('isConditionalAvailable() is false when isConditionalMediationAvailable is absent', async () => {
  const PKC = function () {};
  const restore = setGlobal('window', { PublicKeyCredential: PKC });
  try {
    const pk = makeWebauthnPasskey({ rpId: 'gumption.com', rpName: 'G' });
    assert.equal(await pk.isConditionalAvailable(), false);
  } finally {
    restore();
  }
});

test('enroll() honors a residentKey preference and defaults to "required"', async () => {
  let seenSelection = null;
  const fakeNav = {
    credentials: {
      create: async (opts) => {
        seenSelection = opts.publicKey.authenticatorSelection;
        return {
          rawId: new Uint8Array([1, 2, 3]).buffer,
          getClientExtensionResults: () => ({ prf: { results: { first: new Uint8Array(32).fill(9).buffer } } }),
        };
      },
    },
  };
  const restore = setGlobal('navigator', fakeNav);
  try {
    const pk = makeWebauthnPasskey({ rpId: 'gumption.com', rpName: 'G' });
    await pk.enroll({ userName: 'a' });
    assert.equal(seenSelection.residentKey, 'required');
    await pk.enroll({ userName: 'a', residentKey: 'discouraged' });
    assert.equal(seenSelection.residentKey, 'discouraged');
  } finally {
    restore();
  }
});
