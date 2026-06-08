// DEK-wrapping multi-method keyring for the persistent browser wallet. ONE
// persisted wallet, unlockable by EITHER a passphrase OR a passkey — the same
// wallet, either method. Composes the existing primitives; no crypto is
// duplicated.
//
// Scheme:
//   - A random 32-byte DEK (AES-GCM) encrypts the wallet's b58 -> wallet_ct.
//   - The DEK's raw bytes are wrapped SEPARATELY per method's KEK:
//       passphrase -> deriveKey (PBKDF2 -> AES-GCM)        -> sealWithKey(KEK, dekRaw)
//   - unlock(method, secret): derive that KEK -> openWithKey the wrap -> dekRaw
//     -> import DEK -> openWithKey(DEK, wallet_ct) -> b58 -> Wallet.
//
// The stored record is ALWAYS ciphertext; the plaintext b58/DEK exist only
// transiently in function scope. A wrong secret fails closed via the AES-GCM
// auth tag (openWithKey rejects) — never a partial/garbage wallet.
//
// Stored record (IndexedDB structured-cloneable; iv/ciphertext are Uint8Array):
//   { version, address, wallet_ct: {iv, ciphertext},
//     wraps: { passphrase?: {salt, iterations, iv, ciphertext} } }
//
// DOM-free: store is INJECTED (like gc-store) so the whole flow is Node-testable
// with fakes.
//   store (single record): get()->record|null, put(record), delete().
import { NoWalletError } from './gc-errors.mjs';
import { sealWithKey, openWithKey } from './gc-envelope.mjs';
import { deriveKey } from './gc-backup.mjs';
import { Wallet } from './gc-wallet.mjs';

const VERSION = 1;
const SALT_BYTES = 16;
const DEK_BYTES = 32;
const PBKDF2_ITERATIONS = 600000;
const te = new TextEncoder();
const td = new TextDecoder();

// Re-export so consumers/tests can import the typed errors from here.
export { NoWalletError } from './gc-errors.mjs';

async function newDek() {
  const raw = crypto.getRandomValues(new Uint8Array(DEK_BYTES));
  return { raw, key: await importDek(raw) };
}

async function importDek(raw) {
  return crypto.subtle.importKey('raw', raw, 'AES-GCM', false, [
    'encrypt',
    'decrypt',
  ]);
}

async function passphraseWrap(passphrase, dekRaw) {
  const salt = crypto.getRandomValues(new Uint8Array(SALT_BYTES));
  const kek = await deriveKey(passphrase, salt, PBKDF2_ITERATIONS);
  const { iv, ciphertext } = await sealWithKey(kek, dekRaw);
  return { salt, iterations: PBKDF2_ITERATIONS, iv, ciphertext };
}

export async function hasWallet(store) {
  return (await store.get()) !== null;
}

// Create the record + the mandatory passphrase wrap. The passphrase is the
// universal floor; a passkey is added later via addPasskey.
export async function enroll(wallet, { store }, { passphrase }) {
  const b58 = await wallet.exportPrivateKeyB58();
  const { raw: dekRaw, key: dekKey } = await newDek();
  const wallet_ct = await sealWithKey(dekKey, te.encode(b58));
  const wraps = { passphrase: await passphraseWrap(passphrase, dekRaw) };
  await store.put({
    version: VERSION,
    address: await wallet.address(),
    wallet_ct,
    wraps,
  });
  return wallet;
}

// Unwrap the DEK raw bytes with the supplied passphrase. Wrong secret -> GCM
// auth-tag failure -> openWithKey rejects (fails closed).
async function unwrapDek(rec, { passphrase } = {}) {
  const { wraps } = rec;
  if (passphrase != null && wraps.passphrase) {
    const w = wraps.passphrase;
    const kek = await deriveKey(passphrase, w.salt, w.iterations);
    return new Uint8Array(await openWithKey(kek, w));
  }
  throw new Error('no usable unlock method/secret');
}

function loadRecord(rec) {
  if (rec === null) {
    throw new NoWalletError('no stored wallet');
  }
  if (rec.version !== VERSION) {
    // Fail fast on an unknown/corrupt record rather than mis-decoding fields;
    // a future schema bump handles migration here explicitly.
    throw new Error(`unsupported wallet record version: ${rec.version}`);
  }
  return rec;
}

export async function unlock({ store } = {}, { passphrase } = {}) {
  const rec = loadRecord(await store.get());
  const dekRaw = await unwrapDek(rec, { passphrase });
  const dekKey = await importDek(dekRaw);
  const b58 = td.decode(new Uint8Array(await openWithKey(dekKey, rec.wallet_ct)));
  return Wallet.fromPrivateKeyB58(b58);
}

export async function clear(store) {
  await store.delete();
}
