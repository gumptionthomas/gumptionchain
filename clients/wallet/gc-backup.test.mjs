import { test } from 'node:test';
import assert from 'node:assert/strict';
import { Wallet } from './gc-wallet.mjs';
import { base64decode, base64encode } from './gc-crypto.mjs';
import {
  exportEncrypted, importEncrypted, deriveKey,
  BadBackupError, BadPassphraseError,
} from './gc-backup.mjs';
import { sealWithKey, openWithKey } from './gc-envelope.mjs';

// Keep PBKDF2 cheap in tests; production default (600k) is exercised by the
// manual browser path. The override is the documented opts.iterations seam.
const FAST = { iterations: 1000 };
const PASS = 'correct horse battery staple';

test('exported deriveKey (PBKDF2) is deterministic for a fixed salt/iterations', async () => {
  // The keyring reuses deriveKey to wrap the DEK under a passphrase-KEK; assert
  // the exported derivation is stable (same salt+iterations+passphrase -> a key
  // that decrypts the other's ciphertext) and salt-sensitive (fails closed).
  const salt = new Uint8Array(16).fill(3);
  const k1 = await deriveKey(PASS, salt, FAST.iterations);
  const k2 = await deriveKey(PASS, salt, FAST.iterations);
  const env = await sealWithKey(k1, new TextEncoder().encode('dek-raw'));
  assert.deepEqual(
    await openWithKey(k2, env), new TextEncoder().encode('dek-raw'),
  );
  const kOther = await deriveKey(PASS, new Uint8Array(16).fill(9), FAST.iterations);
  await assert.rejects(() => openWithKey(kOther, env));
});

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
  // Flip a content byte so GCM's auth tag rejects it (a real bit-flip, not a
  // length/padding change).
  const ct = base64decode(backup.ciphertext);
  ct[0] ^= 0xff;
  backup.ciphertext = base64encode(ct);
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

test('importEncrypted rejects a non-positive kdf.iterations', async () => {
  const wallet = await Wallet.generate();
  const backup = await exportEncrypted(wallet, PASS, FAST);
  backup.kdf.iterations = 0;
  await assert.rejects(() => importEncrypted(backup, PASS), BadBackupError);
});

test('importEncrypted rejects an unexpected kdf.hash', async () => {
  const wallet = await Wallet.generate();
  const backup = await exportEncrypted(wallet, PASS, FAST);
  backup.kdf.hash = 'SHA-512';
  await assert.rejects(() => importEncrypted(backup, PASS), BadBackupError);
});

test('importEncrypted maps un-decodable base64 to BadBackupError', async () => {
  const wallet = await Wallet.generate();
  const backup = await exportEncrypted(wallet, PASS, FAST);
  backup.ciphertext = '!!! not base64 !!!';
  await assert.rejects(() => importEncrypted(backup, PASS), BadBackupError);
});

import { exportPlain, importPlain } from './gc-backup.mjs';

test('exportPlain equals the wallet b58 private key', async () => {
  const wallet = await Wallet.generate();
  assert.equal(await exportPlain(wallet), await wallet.exportPrivateKeyB58());
});

test('exportPlain -> importPlain recovers a wallet that signs identically', async () => {
  const wallet = await Wallet.generate();
  const b58 = await exportPlain(wallet);
  const recovered = await importPlain(b58);
  assert.equal(await recovered.address(), await wallet.address());
  const msg = new TextEncoder().encode('prove-it');
  assert.equal(await recovered.sign(msg), await wallet.sign(msg));
});
