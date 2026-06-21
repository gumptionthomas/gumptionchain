// Derive a 32-byte Ed25519 seed from a WebAuthn PRF output (the "self-custodial
// federated login" primitive). HKDF-SHA-256 over the PRF, with an optional
// PBKDF2-stretched passphrase mixed in (2FA). Domain-separated from the
// wrap-keyring's PRF->AES-KEK use (gc-envelope's 'gc-signing-key-aesgcm-v1')
// by distinct HKDF info labels. Fully deterministic — no stored salt — so the
// same passkey reproduces the same seed anywhere. Pure Web Crypto, no deps.
import { encodeSecret } from './gc-bech32.mjs';
import { SigningKey } from './gc-signing-key.mjs';

const te = new TextEncoder();
const PBKDF2_ITERATIONS = 600000;

async function hkdf(ikm, info, length) {
  const key = await crypto.subtle.importKey('raw', ikm, 'HKDF', false, [
    'deriveBits',
  ]);
  const bits = await crypto.subtle.deriveBits(
    {
      name: 'HKDF',
      hash: 'SHA-256',
      salt: new Uint8Array(0),
      info: te.encode(info),
    },
    key,
    length * 8,
  );
  return new Uint8Array(bits);
}

async function pbkdf2(passphrase, salt, length) {
  const key = await crypto.subtle.importKey(
    'raw',
    te.encode(passphrase),
    'PBKDF2',
    false,
    ['deriveBits'],
  );
  const bits = await crypto.subtle.deriveBits(
    { name: 'PBKDF2', hash: 'SHA-256', salt, iterations: PBKDF2_ITERATIONS },
    key,
    length * 8,
  );
  return new Uint8Array(bits);
}

export async function deriveSeed(prfOutput, { passphrase } = {}) {
  if (!(prfOutput instanceof Uint8Array) || prfOutput.length === 0) {
    throw new Error('deriveSeed requires a non-empty PRF output');
  }
  if (passphrase) {
    const passSalt = await hkdf(prfOutput, 'gc-pass-salt-v1', 16);
    const pk = await pbkdf2(passphrase, passSalt, 32);
    const ikm = new Uint8Array(prfOutput.length + 32);
    ikm.set(prfOutput, 0);
    ikm.set(pk, prfOutput.length);
    return hkdf(ikm, 'gc-seed-v1', 32);
  }
  return hkdf(prfOutput, 'gc-seed-v1', 32);
}

export async function deriveSigningKey(prfOutput, opts = {}) {
  const seed = await deriveSeed(prfOutput, opts);
  return SigningKey.fromSecret(encodeSecret(seed));
}
