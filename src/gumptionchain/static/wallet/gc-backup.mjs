// Self-custody wallet backup/recovery. Converts a Wallet <-> a portable
// artifact: a passphrase-encrypted JSON blob (PBKDF2-SHA256 -> AES-GCM-256,
// via the shared gc-envelope primitive) and a raw b58 string. Storage-
// decoupled and Node-testable: re-persist a recovered wallet by composing with
// gc-store.enroll. No dependencies.
import { base64encode, base64decode } from './gc-crypto.mjs';
import { BadBackupError, BadPassphraseError } from './gc-errors.mjs';
import { sealWithKey, openWithKey } from './gc-envelope.mjs';
import { Wallet } from './gc-wallet.mjs';

const BACKUP_VERSION = 1;
const BACKUP_KIND = 'gc-wallet-backup';
const DEFAULT_ITERATIONS = 600000;
const SALT_BYTES = 16;
const te = new TextEncoder();
const td = new TextDecoder();

// Re-export so consumers can import the typed errors from here.
export { BadBackupError, BadPassphraseError } from './gc-errors.mjs';

async function deriveKey(passphrase, salt, iterations) {
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

export async function exportEncrypted(wallet, passphrase, opts = {}) {
  const iterations = opts.iterations ?? DEFAULT_ITERATIONS;
  const salt = crypto.getRandomValues(new Uint8Array(SALT_BYTES));
  const key = await deriveKey(passphrase, salt, iterations);
  const { iv, ciphertext } = await sealWithKey(
    key, te.encode(await wallet.exportPrivateKeyB58()),
  );
  return {
    version: BACKUP_VERSION,
    kind: BACKUP_KIND,
    address: await wallet.address(),
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
    throw new BadBackupError('not a gc-wallet-backup artifact');
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
    throw new BadBackupError('malformed gc-wallet-backup artifact');
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
    throw new BadBackupError('malformed gc-wallet-backup artifact');
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
    return await Wallet.fromPrivateKeyB58(td.decode(b58Bytes));
  } catch {
    throw new BadBackupError('decrypted payload is not a valid wallet key');
  }
}

// Raw-string backup: the b58 private key itself. At-rest protection is the
// user's password manager. Thin, documented wrappers over the #2.1 key seam so
// all backup surface lives in one module.
export async function exportPlain(wallet) {
  return wallet.exportPrivateKeyB58();
}

export async function importPlain(b58) {
  return Wallet.fromPrivateKeyB58(b58);
}
