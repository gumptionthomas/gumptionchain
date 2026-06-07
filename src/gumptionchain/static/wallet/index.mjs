// GumptionChain browser wallet — public API barrel.
// Import the supported surface from here. Anything not re-exported below
// (e.g. gc-crypto low-level helpers, gc-envelope seal/open, messageCanonical)
// is internal and may change without notice.
//
// The package `version` is the embedder-API semver; it is INDEPENDENT of the
// wire scheme ids gc-sig-v1 / gc-msg-v1 bound into signatures.
export const version = '0.2.0';

// Identity / keys
export { Wallet } from './gc-wallet.mjs';

// API request signing (gc-sig-v1)
export { canonical, signHeaders } from './gc-sig.mjs';

// Passkey-anchored storage
export { enroll, unlock, hasWallet, clear } from './gc-store.mjs';
export { makeWebauthnPasskey } from './gc-passkey-webauthn.mjs';
export { makeIdbStore } from './gc-store-idb.mjs';

// Backup / recovery
export {
  exportEncrypted, importEncrypted, exportPlain, importPlain,
} from './gc-backup.mjs';

// Message signing (gc-msg-v1)
export {
  signMessage, verifyMessage, toArmored, fromArmored,
} from './gc-message.mjs';

// Stake attestations (gc-msg-v1 + on-chain provenance composition)
export {
  signStakeAttestation, parseStakeAttestation, verifyStake,
} from './gc-attestation.mjs';

// Typed errors
export {
  UnsupportedError,
  NoWalletError,
  BadBackupError,
  BadPassphraseError,
  BadProofError,
  BadAttestationError,
} from './gc-errors.mjs';
