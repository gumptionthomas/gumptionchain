// Generic gc-msg-v1 message signing: sign arbitrary text into a portable proof
// that verifies off-chain in JS and Python. Domain-separated from gc-sig-v1.
// Pure Web Crypto + vanilla JS. No dependencies. Knows nothing about the chain.
import { sha256Hex, base64encode, base64decode } from './gc-crypto.mjs';
import { BadProofError } from './gc-errors.mjs';
import { Wallet } from './gc-wallet.mjs';

const MSG_SCHEME = 'gc-msg-v1';
const MSG_VERSION = '1';
const te = new TextEncoder();
const td = new TextDecoder();

export { BadProofError } from './gc-errors.mjs';

export async function messageCanonical({ address, timestamp, message }) {
  const digest = await sha256Hex(te.encode(message));
  return te.encode(
    [MSG_SCHEME, MSG_VERSION, address, String(timestamp), digest].join('\n'),
  );
}

export async function signMessage(wallet, message, { timestamp } = {}) {
  const ts = String(timestamp ?? Math.floor(Date.now() / 1000));
  const address = await wallet.address();
  const bytes = await messageCanonical({ address, timestamp: ts, message });
  return {
    scheme: MSG_SCHEME,
    version: MSG_VERSION,
    address,
    public_key: await wallet.publicKeyB64(),
    timestamp: ts,
    message,
    signature: await wallet.sign(bytes),
  };
}

export async function verifyMessage(proof, { maxAge, now } = {}) {
  if (!proof || typeof proof !== 'object') {
    throw new BadProofError('not a proof object');
  }
  const {
    scheme, version, address, public_key: publicKey, timestamp, message, signature,
  } = proof;
  if (
    scheme !== MSG_SCHEME
    || version !== MSG_VERSION
    || typeof address !== 'string'
    || typeof publicKey !== 'string'
    || typeof timestamp !== 'string'
    || typeof message !== 'string'
    || typeof signature !== 'string'
  ) {
    throw new BadProofError('malformed gc-msg-v1 proof');
  }
  let verifier;
  try {
    verifier = await Wallet.fromPublicKeyB64(publicKey);
  } catch {
    throw new BadProofError('invalid public key');
  }
  const result = { address, timestamp, message };
  if ((await verifier.address()) !== address) {
    return { ...result, valid: false, reason: 'address-mismatch' };
  }
  const bytes = await messageCanonical({ address, timestamp, message });
  if (!(await verifier.verify(bytes, signature))) {
    return { ...result, valid: false, reason: 'bad-signature' };
  }
  if (maxAge !== undefined) {
    const current = now ?? Math.floor(Date.now() / 1000);
    if (current - Number(timestamp) > maxAge) {
      return { ...result, valid: false, reason: 'expired' };
    }
  }
  return { ...result, valid: true };
}
