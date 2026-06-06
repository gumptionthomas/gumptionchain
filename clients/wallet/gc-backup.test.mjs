import { test } from 'node:test';
import assert from 'node:assert/strict';
import { Wallet } from './gc-wallet.mjs';
import {
  exportEncrypted, importEncrypted, BadBackupError, BadPassphraseError,
} from './gc-backup.mjs';

// Keep PBKDF2 cheap in tests; production default (600k) is exercised by the
// manual browser path. The override is the documented opts.iterations seam.
const FAST = { iterations: 1000 };
const PASS = 'correct horse battery staple';

test('exportEncrypted -> importEncrypted recovers a wallet that signs identically', async () => {
  const wallet = await Wallet.generate();
  const backup = await exportEncrypted(wallet, PASS, FAST);
  assert.equal(backup.kind, 'gc-wallet-backup');
  assert.equal(backup.version, 1);
  assert.equal(backup.address, await wallet.address());

  const recovered = await importEncrypted(backup, PASS);
  assert.equal(await recovered.address(), await wallet.address());
  const msg = new TextEncoder().encode('prove-it');
  assert.equal(await recovered.sign(msg), await wallet.sign(msg));
});

test('the backup artifact holds only ciphertext + non-secrets', async () => {
  const wallet = await Wallet.generate();
  const backup = await exportEncrypted(wallet, PASS, FAST);
  assert.ok(backup.kdf.salt && backup.iv && backup.ciphertext);
  assert.equal(backup.kdf.name, 'PBKDF2');
  const blob = JSON.stringify(backup);
  assert.ok(!blob.includes(await wallet.exportPrivateKeyB58()));
});

test('importEncrypted with a wrong passphrase throws BadPassphraseError', async () => {
  const wallet = await Wallet.generate();
  const backup = await exportEncrypted(wallet, PASS, FAST);
  await assert.rejects(
    () => importEncrypted(backup, 'wrong passphrase'),
    BadPassphraseError,
  );
});

test('importEncrypted on a tampered ciphertext throws BadPassphraseError', async () => {
  const wallet = await Wallet.generate();
  const backup = await exportEncrypted(wallet, PASS, FAST);
  // flip a byte in the base64 ciphertext's decoded form via a known-bad value
  backup.ciphertext = backup.ciphertext.slice(0, -2) + 'AA';
  await assert.rejects(() => importEncrypted(backup, PASS), BadPassphraseError);
});

test('two exports of the same wallet use distinct salt and IV', async () => {
  const wallet = await Wallet.generate();
  const a = await exportEncrypted(wallet, PASS, FAST);
  const b = await exportEncrypted(wallet, PASS, FAST);
  assert.notEqual(a.kdf.salt, b.kdf.salt);
  assert.notEqual(a.iv, b.iv);
});

test('importEncrypted rejects an unknown kind', async () => {
  await assert.rejects(
    () => importEncrypted({ kind: 'something-else', version: 1 }, PASS),
    BadBackupError,
  );
});

test('importEncrypted rejects an unknown version', async () => {
  const wallet = await Wallet.generate();
  const backup = await exportEncrypted(wallet, PASS, FAST);
  backup.version = 999;
  await assert.rejects(() => importEncrypted(backup, PASS), BadBackupError);
});

test('importEncrypted rejects a malformed artifact (missing fields)', async () => {
  await assert.rejects(
    () => importEncrypted({ kind: 'gc-wallet-backup', version: 1 }, PASS),
    BadBackupError,
  );
});
