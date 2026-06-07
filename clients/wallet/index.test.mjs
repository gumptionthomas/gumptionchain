import { test } from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import * as api from './index.mjs';

const FUNCTIONS = [
  'canonical', 'signHeaders',
  'enroll', 'unlock', 'hasWallet', 'clear',
  'makeWebauthnPasskey', 'makeIdbStore',
  'exportEncrypted', 'importEncrypted', 'exportPlain', 'importPlain',
  'signMessage', 'verifyMessage', 'toArmored', 'fromArmored',
];
const ERRORS = [
  'UnsupportedError', 'NoWalletError', 'BadBackupError',
  'BadPassphraseError', 'BadProofError',
];

test('barrel exports every public function', () => {
  for (const name of FUNCTIONS) {
    assert.equal(typeof api[name], 'function', `missing function: ${name}`);
  }
});

test('barrel exports Wallet as a class and the typed errors', () => {
  assert.equal(typeof api.Wallet, 'function');
  assert.equal(typeof api.Wallet.generate, 'function');
  for (const name of ERRORS) {
    assert.equal(typeof api[name], 'function', `missing error: ${name}`);
    assert.ok(
      new api[name]('x') instanceof Error,
      `${name} is not an Error subclass`,
    );
  }
});

test('version is semver and agrees with package.json', () => {
  assert.match(api.version, /^\d+\.\d+\.\d+$/);
  const pkg = JSON.parse(
    readFileSync(new URL('./package.json', import.meta.url)),
  );
  assert.equal(api.version, pkg.version);
});

test('end-to-end through the barrel: generate -> sign -> verify', async () => {
  const w = await api.Wallet.generate();
  const proof = await api.signMessage(w, 'packaged', { timestamp: '1700000000' });
  const r = await api.verifyMessage(proof);
  assert.equal(r.valid, true);
  assert.equal(r.address, await w.address());
});
