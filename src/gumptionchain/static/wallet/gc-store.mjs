// Enroll/unlock orchestration for the browser wallet. Wires the pure
// gc-envelope seal/open over two INJECTED interfaces (passkey, store) so the
// whole lock/unlock flow is Node-testable with fakes. No dependencies.
//
// passkey: isSupported()->bool, enroll(opts)->{credentialId, prfOutput},
//          unlock(credentialId)->prfOutput (Uint8Array).
// store (single record): get()->record|null, put(record), delete().
import { base64encode, base64decode } from './gc-crypto.mjs';
import { NoWalletError, UnsupportedError } from './gc-errors.mjs';
import { seal, open } from './gc-envelope.mjs';
import { Wallet } from './gc-wallet.mjs';

const RECORD_VERSION = 1;
const te = new TextEncoder();
const td = new TextDecoder();

// Re-export so existing consumers/tests can import the typed errors from here.
export { NoWalletError, UnsupportedError } from './gc-errors.mjs';

export async function hasWallet(store) {
  return (await store.get()) !== null;
}

export async function enroll(wallet, { passkey, store }, opts) {
  if (!(await passkey.isSupported())) {
    throw new UnsupportedError('passkey PRF is not available on this device');
  }
  const { credentialId, prfOutput } = await passkey.enroll(opts);
  const { iv, ciphertext } = await seal(
    prfOutput,
    te.encode(await wallet.exportPrivateKeyB58()),
  );
  const address = await wallet.address();
  await store.put({
    version: RECORD_VERSION,
    address,
    credentialId,
    iv: base64encode(iv),
    ciphertext: base64encode(ciphertext),
  });
  return address;
}

export async function unlock({ passkey, store }) {
  const rec = await store.get();
  if (rec === null) {
    throw new NoWalletError('no stored wallet');
  }
  if (rec.version !== RECORD_VERSION) {
    // Fail fast on an unknown/corrupt record rather than mis-decoding fields;
    // a future schema bump handles migration here explicitly.
    throw new Error(`unsupported wallet record version: ${rec.version}`);
  }
  const prfOutput = await passkey.unlock(rec.credentialId);
  const b58Bytes = await open(prfOutput, {
    iv: base64decode(rec.iv),
    ciphertext: base64decode(rec.ciphertext),
  });
  return Wallet.fromPrivateKeyB58(td.decode(b58Bytes));
}

export async function clear(store) {
  await store.delete();
}
