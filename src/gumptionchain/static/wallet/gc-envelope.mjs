// Authenticated at-rest encryption for the wallet key. Exposes a key-level
// primitive (sealWithKey/openWithKey) over AES-GCM-256 plus the PRF-keyed
// seal/open wrappers: HKDF-SHA256 derives an AES-GCM-256 key from a 32-byte
// WebAuthn PRF output; random 12-byte IV per seal; GCM's auth tag makes
// tampering fail closed. Pure Web Crypto.
const HKDF_INFO = new TextEncoder().encode('gc-wallet-aesgcm-v1');
const IV_BYTES = 12;

async function deriveAesKey(prfOutput) {
  const ikm = await crypto.subtle.importKey('raw', prfOutput, 'HKDF', false, [
    'deriveKey',
  ]);
  return crypto.subtle.deriveKey(
    // Empty HKDF salt is deliberate and sound (RFC 5869): the PRF output is
    // already high-entropy keying material, and the versioned HKDF_INFO label
    // provides domain separation. The PRF output is never used as the key
    // directly — it is HKDF input keying material.
    { name: 'HKDF', hash: 'SHA-256', salt: new Uint8Array(0), info: HKDF_INFO },
    ikm,
    { name: 'AES-GCM', length: 256 },
    false,
    ['encrypt', 'decrypt'],
  );
}

export async function sealWithKey(key, bytes) {
  const iv = crypto.getRandomValues(new Uint8Array(IV_BYTES));
  const ct = await crypto.subtle.encrypt({ name: 'AES-GCM', iv }, key, bytes);
  return { iv, ciphertext: new Uint8Array(ct) };
}

export async function openWithKey(key, { iv, ciphertext }) {
  const pt = await crypto.subtle.decrypt(
    { name: 'AES-GCM', iv: Uint8Array.from(iv) },
    key,
    Uint8Array.from(ciphertext),
  );
  return new Uint8Array(pt);
}

export async function seal(prfOutput, bytes) {
  return sealWithKey(await deriveAesKey(prfOutput), bytes);
}

export async function open(prfOutput, envelope) {
  return openWithKey(await deriveAesKey(prfOutput), envelope);
}
