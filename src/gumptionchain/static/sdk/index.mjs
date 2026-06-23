// GumptionChain browser SDK — public API barrel.
// Import the supported surface from here. Anything not re-exported below
// (e.g. gc-crypto low-level helpers, gc-envelope seal/open, messageCanonical)
// is internal and may change without notice.
//
// The package `version` is the embedder-API semver; it is INDEPENDENT of the
// wire scheme ids gc-sig-v1 / gc-msg-v1 bound into signatures.
export const version = '0.13.0';

// Identity / keys
export { SigningKey } from './gc-signing-key.mjs';

// Onboarding controller (headless create / back up / restore / unlock / sign-login)
export { makeOnboarding } from './gc-onboarding.mjs';

// API request signing (gc-sig-v1)
export { canonical, signHeaders } from './gc-sig.mjs';

// Passkey-anchored storage
export { enroll, unlock, hasSigningKey, clear } from './gc-store.mjs';
export { makeWebauthnPasskey, recognize } from './gc-passkey-webauthn.mjs';
export { makeIdbStore } from './gc-store-idb.mjs';
export { makeSessionSigner } from './gc-session-signer.mjs';
export { makeSessionStore } from './gc-session-store-idb.mjs';

// Backup / recovery
export {
  exportEncrypted, importEncrypted, exportPlain, importPlain,
} from './gc-backup.mjs';

// PRF derivation + BIP-39 recovery codec
export { deriveSeed, deriveSigningKey } from './gc-derive.mjs';
export { seedToMnemonic, mnemonicToSeed } from './gc-bip39.mjs';

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
  NoSigningKeyError,
  BadBackupError,
  BadPassphraseError,
  BadProofError,
  BadAttestationError,
} from './gc-errors.mjs';
