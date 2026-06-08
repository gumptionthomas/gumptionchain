// DEK-wrapping multi-method keyring for the persistent browser wallet. ONE
// persisted wallet, unlockable by EITHER a passphrase OR a passkey — the same
// wallet, either method. Composes the existing primitives; no crypto is
// duplicated.
//
// Scheme:
//   - A random 32-byte DEK (AES-GCM) encrypts the wallet's b58 -> wallet_ct.
//   - The DEK's raw bytes are wrapped SEPARATELY per method's KEK:
//       passphrase -> deriveKey (PBKDF2 -> AES-GCM)        -> sealWithKey(KEK, dekRaw)
//       passkey    -> deriveAesKey (WebAuthn-PRF -> HKDF)  -> sealWithKey(KEK, dekRaw)
//   - unlock(method, secret): derive that KEK -> openWithKey the wrap -> dekRaw
//     -> import DEK -> openWithKey(DEK, wallet_ct) -> b58 -> Wallet.
//
// The stored record is ALWAYS ciphertext; the plaintext b58/DEK exist only
// transiently in function scope. A wrong secret fails closed via the AES-GCM
// auth tag (openWithKey rejects) — never a partial/garbage wallet.
//
// Stored record (IndexedDB structured-cloneable; iv/ciphertext are Uint8Array):
//   { version, address, wallet_ct: {iv, ciphertext},
//     wraps: { passphrase?: {salt, iterations, iv, ciphertext},
//              passkey?: {credentialId, iv, ciphertext} } }
//
// DOM-free: store + passkey are INJECTED (like gc-store) so the whole flow is
// Node-testable with fakes.
//   store (single record): get()->record|null, put(record), delete().
//   passkey: isSupported()->bool, enroll(ids)->{credentialId, prfOutput},
//            unlock(credentialId)->prfOutput (Uint8Array).
import { NoWalletError, UnsupportedError } from './gc-errors.mjs';
import { sealWithKey, openWithKey, deriveAesKey } from './gc-envelope.mjs';
import { deriveKey } from './gc-backup.mjs';
import { Wallet } from './gc-wallet.mjs';

const VERSION = 1;
const SALT_BYTES = 16;
const DEK_BYTES = 32;
const PBKDF2_ITERATIONS = 600000;
const te = new TextEncoder();
const td = new TextDecoder();

// Re-export so consumers/tests can import the typed errors from here.
export { NoWalletError, UnsupportedError } from './gc-errors.mjs';

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

async function passkeyWrap(passkey, dekRaw, ids) {
  if (!(await passkey.isSupported())) {
    throw new UnsupportedError('passkey PRF is not available on this device');
  }
  const { credentialId, prfOutput } = await passkey.enroll(ids);
  const kek = await deriveAesKey(prfOutput);
  const { iv, ciphertext } = await sealWithKey(kek, dekRaw);
  return { credentialId, iv, ciphertext };
}

export async function hasWallet(store) {
  return (await store.get()) !== null;
}

// Create the record + the mandatory passphrase wrap. The passphrase is the
// universal floor; passkey is added later via addPasskey.
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

// Unwrap the DEK raw bytes with whichever method's secret was supplied. An
// explicitly-supplied passphrase is preferred; otherwise the passkey is used.
// Wrong secret -> GCM auth-tag failure -> openWithKey rejects (fails closed).
async function unwrapDek(rec, { passkey } = {}, { passphrase } = {}) {
  const { wraps } = rec;
  if (passphrase != null && wraps.passphrase) {
    const w = wraps.passphrase;
    const kek = await deriveKey(passphrase, w.salt, w.iterations);
    return new Uint8Array(await openWithKey(kek, w));
  }
  if (passkey && wraps.passkey) {
    const prfOutput = await passkey.unlock(wraps.passkey.credentialId);
    const kek = await deriveAesKey(prfOutput);
    return new Uint8Array(await openWithKey(kek, wraps.passkey));
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

export async function unlock({ store, passkey } = {}, { passphrase } = {}) {
  const rec = loadRecord(await store.get());
  const dekRaw = await unwrapDek(rec, { passkey }, { passphrase });
  const dekKey = await importDek(dekRaw);
  const b58 = td.decode(new Uint8Array(await openWithKey(dekKey, rec.wallet_ct)));
  return Wallet.fromPrivateKeyB58(b58);
}

// Add a passkey wrap to an already-enrolled wallet. Unwrap the DEK via the
// supplied passphrase, then wrap the SAME dekRaw under the passkey PRF and
// merge wraps.passkey. The DEK never changes, so both methods unlock the same
// wallet. A wrong passphrase rejects before any record mutation (fails closed).
export async function addPasskey({ store, passkey }, { passphrase }, ids) {
  const rec = loadRecord(await store.get());
  const dekRaw = await unwrapDek(rec, {}, { passphrase });
  const passkeyWrapped = await passkeyWrap(passkey, dekRaw, ids);
  rec.wraps = { ...rec.wraps, passkey: passkeyWrapped };
  await store.put(rec);
  return rec.address;
}

// Add (or replace) the passphrase wrap, unwrapping the DEK via the passkey. The
// symmetric counterpart of addPasskey.
export async function addPassphrase({ store, passkey }, { passphrase }) {
  const rec = loadRecord(await store.get());
  const dekRaw = await unwrapDek(rec, { passkey }, {});
  rec.wraps = { ...rec.wraps, passphrase: await passphraseWrap(passphrase, dekRaw) };
  await store.put(rec);
  return rec.address;
}

// Remove one method's wrap. Refuses to remove the only remaining method (the
// wallet would become permanently unreachable) or a method that isn't enrolled.
export async function removeMethod(store, name) {
  const rec = loadRecord(await store.get());
  if (!rec.wraps[name]) {
    const msg = `no '${name}' method to remove`;
    throw new Error(msg);
  }
  const remaining = Object.keys(rec.wraps).filter((k) => k !== name);
  if (remaining.length === 0) {
    const msg = `refusing to remove the last method ('${name}')`;
    throw new Error(msg);
  }
  const wraps = { ...rec.wraps };
  delete wraps[name];
  rec.wraps = wraps;
  await store.put(rec);
  return rec.address;
}

export async function clear(store) {
  await store.delete();
}
