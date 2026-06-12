// Self-custody signing_key backup/recovery. Converts a SigningKey <-> a portable
// artifact: a passphrase-encrypted JSON blob (PBKDF2-SHA256 -> AES-GCM-256,
// via the shared gc-envelope primitive) and a raw b58 string. Storage-
// decoupled and Node-testable: re-persist a recovered signing_key by composing with
// gc-store.enroll. No dependencies.
import { base64encode, base64decode } from './gc-crypto.mjs';
import { BadBackupError, BadPassphraseError } from './gc-errors.mjs';
import { sealWithKey, openWithKey } from './gc-envelope.mjs';
import { SigningKey } from './gc-signing-key.mjs';

// VERSION 2: the signing-key rename (EGU #265) changed BACKUP_KIND. Bumping
// the format version makes any pre-rename backup fail loudly on import rather
// than decode ambiguously against the new kind string.
const BACKUP_VERSION = 2;
const BACKUP_KIND = 'gc-signing-key-backup';
const DEFAULT_ITERATIONS = 600000;
const SALT_BYTES = 16;
const te = new TextEncoder();
const td = new TextDecoder();

// Re-export so consumers can import the typed errors from here.
export { BadBackupError, BadPassphraseError } from './gc-errors.mjs';

export async function deriveKey(passphrase, salt, iterations) {
  const ikm = await crypto.subtle.importKey(
    'raw', te.encode(passphrase), 'PBKDF2', false, ['deriveKey'],
  );
  return crypto.subtle.deriveKey(
    { name: 'PBKDF2', hash: 'SHA-256', salt: Uint8Array.from(salt), iterations },
    ikm,
    { name: 'AES-GCM', length: 256 },
    false,
    ['encrypt', 'decrypt'],
  );
}

export async function exportEncrypted(signing_key, passphrase, opts = {}) {
  const iterations = opts.iterations ?? DEFAULT_ITERATIONS;
  const salt = crypto.getRandomValues(new Uint8Array(SALT_BYTES));
  const key = await deriveKey(passphrase, salt, iterations);
  const { iv, ciphertext } = await sealWithKey(
    key, te.encode(await signing_key.exportPrivateKeyB58()),
  );
  return {
    version: BACKUP_VERSION,
    kind: BACKUP_KIND,
    address: await signing_key.address(),
    kdf: {
      name: 'PBKDF2',
      hash: 'SHA-256',
      iterations,
      salt: base64encode(salt),
    },
    iv: base64encode(iv),
    ciphertext: base64encode(ciphertext),
  };
}

export async function importEncrypted(backup, passphrase) {
  if (!backup || backup.kind !== BACKUP_KIND) {
    throw new BadBackupError('not a gc-signing-key-backup artifact');
  }
  if (backup.version !== BACKUP_VERSION) {
    throw new BadBackupError(`unsupported backup version: ${backup.version}`);
  }
  const { kdf, iv, ciphertext } = backup;
  if (
    !kdf
    || kdf.name !== 'PBKDF2'
    || kdf.hash !== 'SHA-256'
    || typeof kdf.salt !== 'string'
    || !Number.isSafeInteger(kdf.iterations)
    || kdf.iterations <= 0
    || typeof iv !== 'string'
    || typeof ciphertext !== 'string'
  ) {
    throw new BadBackupError('malformed gc-signing-key-backup artifact');
  }
  // Decode the structural base64 fields up front: a decode failure here is a
  // malformed artifact (BadBackupError), distinct from an authentic-but-wrong
  // passphrase (BadPassphraseError) detected by the GCM tag below.
  let salt;
  let ivBytes;
  let ctBytes;
  try {
    salt = base64decode(kdf.salt);
    ivBytes = base64decode(iv);
    ctBytes = base64decode(ciphertext);
  } catch {
    throw new BadBackupError('malformed gc-signing-key-backup artifact');
  }
  const key = await deriveKey(passphrase, salt, kdf.iterations);
  let b58Bytes;
  try {
    b58Bytes = await openWithKey(key, { iv: ivBytes, ciphertext: ctBytes });
  } catch {
    // GCM tag mismatch: wrong passphrase or tampered backup. Fail closed.
    throw new BadPassphraseError('wrong passphrase or corrupt backup');
  }
  try {
    // GCM already authenticated the plaintext, so this should always succeed;
    // map any residual decode/key failure to BadBackupError defensively.
    return await SigningKey.fromPrivateKeyB58(td.decode(b58Bytes));
  } catch {
    throw new BadBackupError('decrypted payload is not a valid signing_key key');
  }
}

// Raw-string backup: the b58 private key itself. At-rest protection is the
// user's password manager. Thin, documented wrappers over the #2.1 key seam so
// all backup surface lives in one module.
export async function exportPlain(signing_key) {
  return signing_key.exportPrivateKeyB58();
}

export async function importPlain(b58) {
  return SigningKey.fromPrivateKeyB58(b58);
}
