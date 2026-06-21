// Generic gc-msg-v1 message signing: sign arbitrary text into a portable proof
// that verifies off-chain in JS and Python. Domain-separated from gc-sig-v2.
// Pure Web Crypto + vanilla JS. No dependencies. Knows nothing about the chain.
import { sha256Hex, base64encode, base64decode } from './gc-crypto.mjs';
import { BadProofError } from './gc-errors.mjs';
import { SigningKey } from './gc-signing-key.mjs';

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

export async function signMessage(signing_key, message, { timestamp } = {}) {
  const ts = String(timestamp ?? Math.floor(Date.now() / 1000));
  const address = await signing_key.address();
  const bytes = await messageCanonical({ address, timestamp: ts, message });
  return {
    scheme: MSG_SCHEME,
    version: MSG_VERSION,
    address,
    timestamp: ts,
    message,
    signature: await signing_key.sign(bytes),
  };
}

export async function verifyMessage(proof, { maxAge, now } = {}) {
  if (!proof || typeof proof !== 'object') {
    throw new BadProofError('not a proof object');
  }
  const {
    scheme, version, address, timestamp, message, signature,
  } = proof;
  if (
    scheme !== MSG_SCHEME
    || version !== MSG_VERSION
    || typeof address !== 'string'
    || typeof timestamp !== 'string'
    || !/^[0-9]+$/.test(timestamp)
    || typeof message !== 'string'
    || typeof signature !== 'string'
  ) {
    throw new BadProofError('malformed gc-msg-v1 proof');
  }
  let verifier;
  try {
    verifier = await SigningKey.fromAddress(address);
  } catch {
    throw new BadProofError('invalid address');
  }
  const result = { address, timestamp, message };
  const bytes = await messageCanonical({ address, timestamp, message });
  let signatureOk;
  try {
    signatureOk = await verifier.verify(bytes, signature);
  } catch {
    // A non-base64 / malformed signature string fails verification; mirror
    // Python (which catches binascii errors) rather than leaking an exception.
    signatureOk = false;
  }
  if (!signatureOk) {
    return { ...result, valid: false, reason: 'bad-signature' };
  }
  if (maxAge !== undefined) {
    const current = now ?? Math.floor(Date.now() / 1000);
    // Symmetric window: reject stale AND future timestamps (mirrors the
    // server-side gc-sig-v2 freshness check) so maxAge can't be defeated by a
    // far-future signed timestamp.
    if (Math.abs(current - Number(timestamp)) > maxAge) {
      return { ...result, valid: false, reason: 'expired' };
    }
  }
  return { ...result, valid: true };
}

const ARMOR_HEADER = '-----BEGIN GUMPTION SIGNED MESSAGE-----';
const ARMOR_SIG = '-----BEGIN GUMPTION SIGNATURE-----';
const ARMOR_FOOTER = '-----END GUMPTION SIGNED MESSAGE-----';

export function toArmored(proof) {
  const blob = base64encode(te.encode(JSON.stringify(proof)));
  return [ARMOR_HEADER, proof.message, ARMOR_SIG, blob, ARMOR_FOOTER].join('\n');
}

export function fromArmored(text) {
  const lines = text.replace(/\r\n/g, '\n').split('\n');
  const h = lines.indexOf(ARMOR_HEADER);
  const s = lines.indexOf(ARMOR_SIG);
  const f = lines.indexOf(ARMOR_FOOTER);
  if (h < 0 || s < 0 || f < 0 || !(h < s && s < f)) {
    throw new BadProofError('malformed armored message');
  }
  const cleartext = lines.slice(h + 1, s).join('\n');
  const blob = lines.slice(s + 1, f).join('').trim();
  let proof;
  try {
    proof = JSON.parse(td.decode(base64decode(blob)));
  } catch {
    throw new BadProofError('malformed armored signature block');
  }
  if (proof.message !== cleartext) {
    throw new BadProofError('armored cleartext does not match signed message');
  }
  return proof;
}
