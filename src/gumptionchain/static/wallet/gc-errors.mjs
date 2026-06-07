// Typed errors shared by the wallet storage orchestration (gc-store) and the
// real adapters (gc-passkey-webauthn). Kept in their own module so an adapter
// can throw the typed error without importing the whole orchestration graph.

// PRF/WebAuthn unavailable, or an assertion returned no PRF result — distinct
// from a user-cancel/abort DOMException, so callers can branch on it.
export class UnsupportedError extends Error {}

// unlock() called with nothing stored.
export class NoWalletError extends Error {}

// Backup artifact is not a recognizable gc-wallet-backup, has an unknown
// version, or is missing required fields — thrown before any crypto.
export class BadBackupError extends Error {}

// importEncrypted decrypt failed (wrong passphrase or tampered backup) —
// re-thrown from the GCM tag mismatch so callers never see garbage plaintext.
export class BadPassphraseError extends Error {}

// Input is not a structurally valid gc-msg-v1 proof (un-parseable / wrong
// shape) — distinct from a proof that parses but fails verification.
export class BadProofError extends Error {}

// Input is not a structurally valid stake attestation (bad claim shape or a
// proof whose message is not a parseable claim).
export class BadAttestationError extends Error {}
