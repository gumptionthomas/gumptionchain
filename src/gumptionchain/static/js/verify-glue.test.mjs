import { test } from 'node:test';
import assert from 'node:assert/strict';
import { nodeFetchProvenance, renderVerdict } from './verify-glue.mjs';

const TX = 'a'.repeat(64);

// Swap in a stub fetch for the duration of fn, always restoring the original
// afterward so tests don't bleed global state into each other.
async function withFetch(stub, fn) {
  const orig = globalThis.fetch;
  globalThis.fetch = stub;
  try {
    return await fn();
  } finally {
    globalThis.fetch = orig;
  }
}

test('nodeFetchProvenance returns null on 404', async () => {
  await withFetch(
    async () => ({ status: 404, ok: false }),
    async () => assert.equal(await nodeFetchProvenance('')(TX), null),
  );
});

test('nodeFetchProvenance throws on non-ok, non-404', async () => {
  await withFetch(
    async () => ({ status: 500, ok: false }),
    async () =>
      assert.rejects(
        () => nodeFetchProvenance('')(TX),
        /provenance fetch failed: 500/,
      ),
  );
});

test('nodeFetchProvenance encodes the txid path segment', async () => {
  let captured;
  await withFetch(
    async (url) => {
      captured = url;
      return { status: 200, ok: true, json: async () => ({}) };
    },
    async () => {
      await nodeFetchProvenance('http://x')('a/b?c#d');
    },
  );
  assert.equal(captured, 'http://x/transaction/a%2Fb%3Fc%23d/provenance.json');
});

// Minimal DOM stand-in: querySelector returns a cached fake element per
// selector, so renderVerdict and assertions see the same nodes.
function fakeRoot() {
  const nodes = {};
  return {
    querySelector(sel) {
      nodes[sel] ??= {
        textContent: '',
        classList: {
          flags: {},
          toggle(name, on) {
            this.flags[name] = on;
          },
        },
      };
      return nodes[sel];
    },
  };
}

test('renderVerdict toggles checks/seal and writes reasons', () => {
  const root = fakeRoot();
  renderVerdict(
    {
      checks: { signature: true, onchain: true, consistent: false },
      valid: false,
      reasons: ['claim-mismatch'],
    },
    root,
  );
  assert.equal(
    root.querySelector('[data-check="signature"]').classList.flags[
      'check-pass'
    ],
    true,
  );
  assert.equal(
    root.querySelector('[data-check="consistent"]').classList.flags[
      'check-fail'
    ],
    true,
  );
  assert.equal(
    root.querySelector('#verdict-seal').classList.flags.verified,
    false,
  );
  assert.equal(
    root.querySelector('#verdict-reasons').textContent,
    'claim-mismatch',
  );
});

test('renderVerdict reasons line defaults to all-checks-passed', () => {
  const root = fakeRoot();
  renderVerdict(
    {
      checks: { signature: true, onchain: true, consistent: true },
      valid: true,
      reasons: [],
    },
    root,
  );
  assert.equal(
    root.querySelector('#verdict-reasons').textContent,
    'all checks passed',
  );
});
